"""Bounded deterministic conditioning. Heavy dependencies are worker-local imports."""

from __future__ import annotations

import io
from dataclasses import asdict
from typing import Any

from trade_extract.config import Settings
from trade_extract.domain import errors
from trade_extract.domain.conditioning import (
    ConditioningResult,
    CoordinateTransform,
    QualityMeasurements,
)
from trade_extract.domain.readers import TextCharacter
from trade_extract.services.page_model import PageRendering, _encode_jpeg, _thumbnail


def condition_page(page: PageRendering, settings: Settings) -> ConditioningResult:
    import cv2
    import numpy as np
    from PIL import Image

    policy = tuple(
        (name.removeprefix("conditioning_"), value)
        for name, value in settings.model_dump().items()
        if name.startswith("conditioning_")
    ) + tuple(
        (name, getattr(settings, name))
        for name in ("page_render_dpi", "page_image_quality", "page_thumbnail_max_px")
    )
    try:
        with Image.open(io.BytesIO(page.image_bytes)) as source:
            w, h = source.size
            if w * h > settings.conditioning_max_pixels:
                raise errors.processing_failed(str(page.page_index), "conditioning_pixel_budget")
            image = np.array(source.convert("RGB"))
    except errors.DomainError:
        raise
    except Exception as exc:
        raise errors.processing_failed(str(page.page_index), "conditioning_decode_failed") from exc
    matrix = np.eye(3)
    operations: list[str] = []
    reasons: list[str] = []
    retained_short = min(w, h)
    # PDFium has already applied PDF rotation. Only image EXIF remains pending.
    orientation = page.exif_orientation
    transforms = {
        2: ([-1, 0, w, 0, 1, 0, 0, 0, 1], (w, h)),
        3: ([-1, 0, w, 0, -1, h, 0, 0, 1], (w, h)),
        4: ([1, 0, 0, 0, -1, h, 0, 0, 1], (w, h)),
        5: ([0, 1, 0, 1, 0, 0, 0, 0, 1], (h, w)),
        6: ([0, -1, h, 1, 0, 0, 0, 0, 1], (h, w)),
        7: ([0, -1, h, -1, 0, w, 0, 0, 1], (h, w)),
        8: ([0, 1, 0, -1, 0, w, 0, 0, 1], (h, w)),
    }
    if orientation in transforms:
        values, size = transforms[orientation]
        matrix = np.array(values, dtype=float).reshape(3, 3)
        # Pixel-edge homography must be shifted to pixel centres for rasterisation.
        image = _warp(cv2, np, image, matrix, size)
        operations.append(f"exif_orientation:{orientation}")
    elif orientation != 1:
        reasons.append("unsupported_orientation")
    ah, aw = image.shape[:2]
    ratio = min(1.0, settings.conditioning_analysis_max_px / max(aw, ah))
    small = cv2.resize(image, (max(1, round(aw * ratio)), max(1, round(ah * ratio))))
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    # A unique bright sheet on a dark surround is the only supported border evidence.
    _, mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    quads = []
    for contour in contours:
        area = cv2.contourArea(contour) / gray.size
        quad = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if 0.5 < area < 0.95 and len(quad) == 4 and cv2.isContourConvex(quad):
            quads.append(quad.reshape(-1, 2).astype(float) / ratio)
    outside_uniform = False
    if len(quads) == 1:
        # Retain a small source-pixel margin around the detected edge; this is kept
        # in the derivative, never excluded from the discarded-content check.
        centre = quads[0].mean(axis=0)
        vectors = quads[0] - centre
        quads[0] += 4 * vectors / np.linalg.norm(vectors, axis=1)[:, None]
        # Inspect every source pixel that would be discarded, including the edge band.
        # No dilation: a nearby annotation must not be treated as scanner background.
        native_gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        inside = np.zeros(native_gray.shape, dtype=np.uint8)
        cv2.fillConvexPoly(inside, np.round(quads[0]).astype(np.int32), 255)
        outside = native_gray[inside == 0]
        outside_rgb = image[inside == 0]
        outside_uniform = bool(
            outside.size and np.max(outside) < 100 and np.max(np.ptp(outside_rgb, axis=0)) <= 8
        )
        if not outside_uniform:
            reasons.append("uncertain_border_content")
    if (
        outside_uniform
        and len(quads) == 1
        and float(np.median(np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1])))) < 100
    ):
        pts = quads[0]
        ordered = np.array(
            [
                pts[np.argmin(pts.sum(axis=1))],
                pts[np.argmin(np.diff(pts, axis=1))],
                pts[np.argmax(pts.sum(axis=1))],
                pts[np.argmax(np.diff(pts, axis=1))],
            ],
            dtype=np.float32,
        )
        width = round(
            max(np.linalg.norm(ordered[1] - ordered[0]), np.linalg.norm(ordered[2] - ordered[3]))
        )
        height = round(
            max(np.linalg.norm(ordered[3] - ordered[0]), np.linalg.norm(ordered[2] - ordered[1]))
        )
        retained_short = min(
            retained_short,
            int(min(np.linalg.norm(ordered[(i + 1) % 4] - ordered[i]) for i in range(4))),
        )
        if width > 0 and height > 0 and width * height <= settings.conditioning_max_pixels:
            target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
            correction = cv2.getPerspectiveTransform(ordered, target)
            # A conservative bound on every local Jacobian over the sheet bounds.
            # Scale image sampling, not rotation canvas padding, to honor the policy.
            bound = _magnification_bound(np, correction, ordered)
            reduction = min(1.0, settings.conditioning_max_enlargement / bound)
            size = (max(1, int(width * reduction)), max(1, int(height * reduction)))
            correction = np.diag([size[0] / width, size[1] / height, 1.0]) @ correction
            image = _warp(cv2, np, image, correction, size)
            matrix = correction @ matrix
            operations.append("perspective_dark_surround")
    retained_short = min(retained_short, *image.shape[:2])
    # Horizontal line evidence can establish small skew, but never upright vs upside-down.
    ih, iw = image.shape[:2]
    ratio = min(1.0, settings.conditioning_analysis_max_px / max(iw, ih))
    small = cv2.resize(image, (max(1, round(iw * ratio)), max(1, round(ih * ratio))))
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    lines = cv2.HoughLinesP(
        cv2.Canny(gray, 50, 150),
        1,
        np.pi / 1800,
        80,
        minLineLength=max(40, gray.shape[1] // 5),
        maxLineGap=10,
    )
    angles = (
        []
        if lines is None
        else [
            float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            for x1, y1, x2, y2 in lines.reshape(-1, 4)
            if abs(x2 - x1) > abs(y2 - y1)
        ]
    )
    skew = float(np.median(angles)) if len(angles) >= 3 else 0.0
    if (
        angles
        and (len(angles) < 3 or float(np.std(angles)) >= 1.5)
        and any(abs(a) > 1.5 for a in angles)
    ):
        reasons.append("unresolved_skew")
    if len(angles) >= 3 and float(np.std(angles)) < 1.5 and abs(skew) > 0.25:
        if abs(skew) > settings.conditioning_max_skew:
            reasons.append("excessive_skew")
        else:
            correction = np.eye(3)
            correction[:2] = cv2.getRotationMatrix2D((iw / 2, ih / 2), skew, 1.0)
            corners = np.array([[0, 0, 1], [iw, 0, 1], [iw, ih, 1], [0, ih, 1]]) @ correction.T
            low, high = corners[:, :2].min(axis=0), corners[:, :2].max(axis=0)
            correction[:2, 2] -= low
            size = (int(np.ceil(high[0] - low[0])), int(np.ceil(high[1] - low[1])))
            if size[0] * size[1] > settings.conditioning_max_pixels:
                raise errors.processing_failed(str(page.page_index), "conditioning_pixel_budget")
            image = _warp(cv2, np, image, correction, size)
            matrix = correction @ matrix
            operations.append("deskew")
    if lines is not None:
        vertical = sum(abs(y2 - y1) > 3 * abs(x2 - x1) for x1, y1, x2, y2 in lines.reshape(-1, 4))
        horizontal_strength = sum(
            abs(x2 - x1) for x1, y1, x2, y2 in lines.reshape(-1, 4) if abs(x2 - x1) > abs(y2 - y1)
        )
        vertical_strength = sum(
            abs(y2 - y1)
            for x1, y1, x2, y2 in lines.reshape(-1, 4)
            if abs(y2 - y1) > 3 * abs(x2 - x1)
        )
        if (
            vertical >= 3
            and vertical_strength > 2 * horizontal_strength
            and page.exif_orientation == 1
            and not page.characters
        ):
            reasons.append("ambiguous_orientation")
    # DPI normalization only reduces known oversampling; never enlarge low-detail evidence.
    effective_dpi = min(page.dpi, settings.page_render_dpi)
    if effective_dpi < page.dpi:
        # Metadata alone must not shrink otherwise useful evidence below the detail floor.
        scale = max(
            effective_dpi / page.dpi,
            min(1.0, settings.conditioning_min_short_edge / retained_short),
        )
        effective_dpi = max(1, round(page.dpi * scale))
        ih, iw = image.shape[:2]
        size = (max(1, round(iw * scale)), max(1, round(ih * scale)))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        matrix = np.diag([size[0] / iw, size[1] / ih, 1.0]) @ matrix
        operations.append("dpi_downsample")
    # Edge-preserving low-strength denoise retains colour, stamps and handwriting strokes.
    # Apply only to large images; no component deletion, binarization or content crop.
    if min(image.shape[:2]) >= 600:
        image = cv2.bilateralFilter(image, 3, 5, 5)
        operations.append("edge_preserving_denoise")
    # Preserve stamps/handwriting: do not threshold or erase components. No synthetic detail.
    out = Image.fromarray(image)
    sample = out.copy()
    sample.thumbnail((settings.conditioning_analysis_max_px,) * 2)
    measured = np.array(sample.convert("L"))
    contrast = float(np.percentile(measured, 99.9) - np.percentile(measured, 0.1))
    blur = float(cv2.Laplacian(measured, cv2.CV_64F).var())
    ink = float(np.mean(measured < 200))
    short = min(retained_short, *out.size)
    if short < settings.conditioning_min_short_edge:
        reasons.append("insufficient_source_detail")
    if ink < settings.conditioning_min_ink:
        reasons.append("blank")
    if ink > settings.conditioning_max_ink:
        reasons.append("excessive_darkness")
    if contrast < settings.conditioning_min_contrast:
        reasons.append("low_contrast")
    if blur < settings.conditioning_min_blur:
        reasons.append("blurred")
    measurements = QualityMeasurements(contrast, blur, ink, short, skew)
    score = min(
        1.0,
        contrast / max(1.0, settings.conditioning_min_contrast),
        blur / max(1.0, settings.conditioning_min_blur),
        short / settings.conditioning_min_short_edge,
    )
    return ConditioningResult(
        _encode_jpeg(out, settings.page_image_quality),
        _encode_jpeg(_thumbnail(out, settings.page_thumbnail_max_px), settings.page_image_quality),
        CoordinateTransform(
            (w, h), out.size, tuple(matrix.ravel()), tuple(np.linalg.inv(matrix).ravel())
        ),
        measurements,
        round(score, 4),
        "unreadable" if reasons else "conditioned",
        tuple(reasons),
        tuple(operations),
        policy,
        effective_dpi,
    )


def _magnification_bound(np: Any, matrix: Any, corners: Any) -> float:
    a, b, c, d, e, f, g, h, i = matrix.ravel()
    x, y = corners[:, 0], corners[:, 1]
    z = g * x + h * y + i
    if min(z) <= 0 <= max(z):
        raise ValueError("conditioning_invalid_transform")
    denominator = float(np.min(np.abs(z))) ** 2
    # Jacobian numerators are linear; extrema on a convex quadrilateral are at vertices.
    bounds = (
        np.array(
            [
                [
                    np.max(np.abs((a * h - b * g) * y + a * i - c * g)),
                    np.max(np.abs((b * g - a * h) * x + b * i - c * h)),
                ],
                [
                    np.max(np.abs((d * h - e * g) * y + d * i - f * g)),
                    np.max(np.abs((e * g - d * h) * x + e * i - f * h)),
                ],
            ]
        )
        / denominator
    )
    return float(np.linalg.norm(bounds, 2))


def _warp(cv2: Any, np: Any, image: Any, matrix: Any, size: tuple[int, int]) -> Any:
    shift = np.array([[1.0, 0, 0.5], [0, 1.0, 0.5], [0, 0, 1.0]])
    return cv2.warpPerspective(
        image,
        np.linalg.inv(shift) @ matrix @ shift,
        size,
        flags=cv2.INTER_LINEAR,
        borderValue=(255, 255, 255),
    )


def diagnostics(result: ConditioningResult, page: PageRendering) -> dict[str, Any]:
    return {
        "measurements": asdict(result.measurements),
        "policy": dict(result.policy),
        "operations": list(result.operations),
        "reasons": list(result.reasons),
        "transform": asdict(result.transform),
        "orientation_evidence": "exif" if page.exif_orientation != 1 else "source_order",
        "orientation_uncertainty": "image geometry cannot distinguish 0 from 180 degrees",
        "characters": [
            {"text": c.text, "bbox": result.transform.box(c.bbox).model_dump()}
            for c in page.characters
        ],
    }


def conditioned_characters(
    page: PageRendering, result: ConditioningResult
) -> tuple[TextCharacter, ...]:
    return tuple(TextCharacter(c.text, result.transform.box(c.bbox)) for c in page.characters)
