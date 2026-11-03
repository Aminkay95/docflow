import io
from dataclasses import replace

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from trade_extract.config import Settings
from trade_extract.domain import errors
from trade_extract.domain.conditioning import VERSION
from trade_extract.domain.inference import InferenceRequest, PageImage
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput
from trade_extract.providers.mock import MockProvider
from trade_extract.services.conditioning import condition_page, diagnostics
from trade_extract.services.page_model import PageRendering
from trade_extract.services.readers.vision import VisionReader


def rendering(image, **kwargs):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return PageRendering(0, stream.getvalue(), b"", *image.size, 300, 0, "", **kwargs)


def sheet(size=(800, 1000)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for y in range(100, size[1] - 100, 40):
        draw.line((100, y, size[0] - 100, y), fill="black", width=3)
    draw.rectangle((150, 150, 170, 170), fill="red")
    return image


def test_clean_page_quality_and_policy_are_stable():
    page = rendering(sheet())
    result = condition_page(page, Settings())
    assert result.status == "conditioned"
    assert result.version == VERSION
    assert result.transform.source_size == result.transform.conditioned_size
    assert result.transform.point(150, 150) == (150, 150)
    assert result.image_bytes == condition_page(page, Settings()).image_bytes
    assert "ocr_confidence" not in diagnostics(result, page)["measurements"]
    assert len(Image.open(io.BytesIO(result.thumbnail_bytes)).getbands()) == 3


@pytest.mark.parametrize(
    "orientation,expected", [(3, (650, 850)), (6, (850, 150)), (8, (150, 650))]
)
def test_exif_landmarks_and_inverse_are_independent(orientation, expected):
    result = condition_page(rendering(sheet(), exif_orientation=orientation), Settings())
    assert result.transform.point(150, 150) == pytest.approx(expected, abs=0.01)
    assert result.transform.point(*expected, inverse=True) == pytest.approx((150, 150), abs=0.01)
    # Pixel centres track the independently calculated rotated red landmark to 2 pixels.
    image = np.array(Image.open(io.BytesIO(result.image_bytes)))
    ys, xs = np.where((image[:, :, 0] > 180) & (image[:, :, 1] < 80) & (image[:, :, 2] < 80))
    point = result.transform.point(160, 160)
    assert (xs.mean() + 0.5, ys.mean() + 0.5) == pytest.approx(point, abs=2)


def test_perspective_landmark_alignment_and_inverse():
    # Independent camera projection from rectangular sheet to trapezoid.
    source = np.array(sheet())
    corners = np.float32([[80, 70], [920, 110], [870, 1090], [110, 1120]])
    camera = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [800, 0], [800, 1000], [0, 1000]]), corners
    )
    photograph = cv2.warpPerspective(source, camera, (1000, 1200), borderValue=(20, 20, 20))
    result = condition_page(rendering(Image.fromarray(photograph)), Settings())
    assert "perspective_dark_surround" in result.operations
    landmark = camera @ np.array([160, 160, 1.0])
    landmark = landmark[:2] / landmark[2]
    expected = (
        160 / 800 * result.transform.conditioned_size[0],
        160 / 1000 * result.transform.conditioned_size[1],
    )
    actual = result.transform.point(*landmark)
    assert actual == pytest.approx(expected, abs=3)
    assert result.transform.point(*actual, inverse=True) == pytest.approx(landmark, abs=0.001)
    image = np.array(Image.open(io.BytesIO(result.image_bytes)))
    ys, xs = np.where((image[:, :, 0] > 180) & (image[:, :, 1] < 80) & (image[:, :, 2] < 80))
    assert (xs.mean() + 0.5, ys.mean() + 0.5) == pytest.approx(actual, abs=3)


def test_skew_corrects_landmarks_without_clipping():
    image = sheet().rotate(6, expand=True, fillcolor="white")
    result = condition_page(rendering(image), Settings())
    assert "deskew" in result.operations
    # All corrected horizontal strokes should have the same y at their endpoints.
    angle = np.radians(6)

    def camera(x, y):
        return (
            np.cos(angle) * (x - 400) + np.sin(angle) * (y - 500) + image.width / 2,
            -np.sin(angle) * (x - 400) + np.cos(angle) * (y - 500) + image.height / 2,
        )

    left = result.transform.point(*camera(100, 100))
    right = result.transform.point(*camera(700, 100))
    assert abs(left[1] - right[1]) < 2
    assert result.transform.point(*left, inverse=True) == pytest.approx(camera(100, 100), abs=0.001)


@pytest.mark.parametrize(
    "image,reason",
    [
        (Image.new("RGB", (800, 1000), "white"), "blank"),
        (sheet((200, 300)), "insufficient_source_detail"),
        (sheet().filter(ImageFilter.GaussianBlur(12)), "blurred"),
    ],
)
def test_poor_pages_refuse_without_provider_calls(image, reason):
    result = condition_page(rendering(image), Settings())
    assert result.status == "unreadable" and reason in result.reasons
    page = PageInput(
        0,
        PageImage(0, result.image_bytes, *result.transform.conditioned_size),
        conditioning_version=result.version,
        conditioning_status=result.status,
        unreadable_reasons=result.reasons,
        transform=result.transform,
    )
    provider = MockProvider()
    request = InferenceRequest(
        document_type="commercial_invoice", source_hash="a" * 64, schema_json={}, evidence=""
    )
    assert VisionReader().read(provider, request, (page,)) is None
    assert provider.requests == []


def test_raw_vision_and_repair_requests_cannot_bypass_conditioning():
    provider = MockProvider()
    request = InferenceRequest(
        document_type="commercial_invoice", source_hash="a" * 64, schema_json={}, evidence=""
    )
    page = PageInput(0, PageImage(0, b"raw", 100, 100))
    assert VisionReader().read(provider, request, (page,)) is None
    assert VisionReader().read(provider, replace(request, repair_errors=("bad",)), (page,)) is None
    assert not provider.requests


def test_configurable_threshold_and_bounded_decode():
    page = rendering(sheet())
    result = condition_page(page, Settings(conditioning_min_short_edge=900))
    assert "insufficient_source_detail" in result.reasons
    assert dict(result.policy)["min_short_edge"] == 900
    with pytest.raises(errors.DomainError, match="conditioning_pixel_budget"):
        condition_page(page, Settings(conditioning_max_pixels=1024))
    with pytest.raises(errors.DomainError, match="conditioning_decode_failed"):
        condition_page(replace(page, image_bytes=b"broken"), Settings())
    with pytest.raises(ValueError):
        Settings(conditioning_min_ink=0.9, conditioning_max_ink=0.2)


def test_box_mapping_matches_independent_rotation():
    result = condition_page(rendering(sheet(), exif_orientation=6), Settings())
    mapped = result.transform.box(BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4))
    assert mapped == BBox(x0=0.6, y0=0.1, x1=0.8, y1=0.3)
    assert result.transform.box(mapped, inverse=True) == BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)


def test_sideways_without_hint_is_refused():
    result = condition_page(rendering(sheet().transpose(Image.Transpose.ROTATE_90)), Settings())
    assert result.status == "unreadable"
    assert "ambiguous_orientation" in result.reasons


def test_low_contrast_is_explicit():
    image = Image.new("RGB", (800, 1000), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    for y in range(100, 900, 40):
        draw.line((100, y, 700, y), fill=(230, 230, 230), width=3)
    result = condition_page(rendering(image), Settings())
    assert result.status == "unreadable"
    assert "low_contrast" in result.reasons


def test_marked_surround_is_not_cropped():
    image = Image.new("RGB", (1000, 1200), (20, 20, 20))
    image.paste(sheet(), (100, 100))
    ImageDraw.Draw(image).text((10, 20), "DO NOT DROP THIS NOTE", fill="white")
    result = condition_page(rendering(image), Settings())
    assert "perspective_dark_surround" not in result.operations
    assert result.transform.conditioned_size == image.size


def test_dpi_downsampling_never_rescues_original_detail():
    page = replace(rendering(sheet()), dpi=600)
    result = condition_page(page, Settings())
    assert result.dpi == 450
    assert "dpi_downsample" in result.operations
    assert result.transform.conditioned_size == (600, 750)
    assert result.transform.point(150, 150) == (112.5, 112.5)
    assert result.status == "conditioned"


def test_adjacent_annotation_is_retained_or_crop_refused():
    image = Image.new("RGB", (1000, 1200), (20, 20, 20))
    image.paste(sheet(), (100, 100))
    ImageDraw.Draw(image).line((98, 400, 98, 500), fill="white", width=1)
    result = condition_page(rendering(image), Settings())
    if "perspective_dark_surround" in result.operations:
        output = np.array(Image.open(io.BytesIO(result.image_bytes)))
        x, y = result.transform.point(98.5, 450.5)
        assert 0 <= x < output.shape[1] and 0 <= y < output.shape[0]
        assert output[round(y), round(x)].mean() > 100
    else:
        assert "uncertain_border_content" in result.reasons


def test_sheet_detail_not_photo_dimensions_controls_resolution():
    image = Image.new("RGB", (800, 1000), (20, 20, 20))
    document = Image.new("RGB", (500, 900), "white")
    draw = ImageDraw.Draw(document)
    for y in range(100, 800, 40):
        draw.line((50, y, 450, y), fill="black", width=3)
    image.paste(document, (150, 50))
    result = condition_page(rendering(image), Settings())
    assert "perspective_dark_surround" in result.operations
    assert result.measurements.source_short_edge < 600
    assert "insufficient_source_detail" in result.reasons


def test_perspective_sampling_respects_enlargement_policy():
    from trade_extract.services.conditioning import _magnification_bound

    quad = np.float32([[100, 100], [900, 100], [650, 1100], [350, 1100]])
    image = cv2.warpPerspective(
        np.array(sheet()),
        cv2.getPerspectiveTransform(np.float32([[0, 0], [800, 0], [800, 1000], [0, 1000]]), quad),
        (1000, 1200),
        borderValue=(20, 20, 20),
    )
    # Keep area over the conservative sheet detector floor.
    quad = np.float32([[80, 50], [920, 50], [800, 1150], [200, 1150]])
    image = cv2.warpPerspective(
        np.array(sheet()),
        cv2.getPerspectiveTransform(np.float32([[0, 0], [800, 0], [800, 1000], [0, 1000]]), quad),
        (1000, 1200),
        borderValue=(20, 20, 20),
    )
    for maximum in (1.0, 1.5):
        result = condition_page(
            rendering(Image.fromarray(image)), Settings(conditioning_max_enlargement=maximum)
        )
        assert "perspective_dark_surround" in result.operations
        # The implementation's conservative analytic bound also bounds independently
        # measured local finite-difference sampling throughout the source sheet.
        matrix = np.array(result.transform.forward).reshape(3, 3)
        assert _magnification_bound(np, matrix, quad) <= maximum + 0.03
        for point in (quad.mean(axis=0), *quad):
            p = np.array(result.transform.point(*point))
            dx = np.array(result.transform.point(*(point + [0.001, 0]))) - p
            dy = np.array(result.transform.point(*(point + [0, 0.001]))) - p
            assert np.linalg.norm(np.stack((dx, dy), axis=1) / 0.001, 2) <= maximum + 0.03


def test_sideways_with_one_horizontal_line_is_still_refused():
    image = sheet().transpose(Image.Transpose.ROTATE_90)
    ImageDraw.Draw(image).line((20, 30, 950, 30), fill="black", width=3)
    result = condition_page(rendering(image), Settings())
    assert "ambiguous_orientation" in result.reasons


def test_conflicting_significant_skew_is_refused():
    image = Image.new("RGB", (900, 1000), "white")
    draw = ImageDraw.Draw(image)
    for index, y in enumerate(range(100, 900, 60)):
        draw.line((100, y, 800, y + (70 if index % 2 else -70)), fill="black", width=3)
    result = condition_page(rendering(image), Settings())
    assert "unresolved_skew" in result.reasons


def test_nonidentity_character_diagnostics_have_independent_rectangle():
    from trade_extract.domain.readers import TextCharacter

    page = rendering(
        sheet(),
        exif_orientation=6,
        characters=(TextCharacter("A", BBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)),),
    )
    result = condition_page(page, Settings(page_image_quality=90, page_thumbnail_max_px=120))
    metadata = diagnostics(result, page)
    assert metadata["characters"] == [{"text": "A", "bbox": dict(x0=0.6, y0=0.1, x1=0.8, y1=0.3)}]
    assert metadata["policy"]["page_render_dpi"] == 300
    assert metadata["policy"]["page_image_quality"] == 90
    assert metadata["policy"]["page_thumbnail_max_px"] == 120


@pytest.mark.parametrize(
    "changes",
    [
        {"forward": (1.0,)},
        {"forward": (float("nan"), 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)},
        {"forward": (0.0,) * 9},
        {"inverse": (2.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)},
        {"source_size": (0, 100)},
    ],
)
def test_malformed_transforms_rejected_before_direct_or_repair_dispatch(changes):
    from trade_extract.domain.conditioning import CoordinateTransform

    with pytest.raises(ValueError, match="conditioning_"):
        CoordinateTransform(
            **({"source_size": (100, 100), "conditioned_size": (100, 100)} | changes)
        )


@pytest.mark.parametrize("repair", [False, True])
def test_image_transform_size_mismatch_cannot_reach_vision(repair):
    from trade_extract.domain.conditioning import CoordinateTransform

    provider = MockProvider()
    page = PageInput(
        0,
        PageImage(0, b"bytes", 100, 200),
        conditioning_status="conditioned",
        conditioning_version=VERSION,
        transform=CoordinateTransform((100, 100), (100, 100)),
    )
    request = InferenceRequest(
        "commercial_invoice", "a" * 64, {}, "", repair_errors=("bad",) if repair else ()
    )
    with pytest.raises(ValueError, match="conditioning_image_dimensions_mismatch"):
        VisionReader().read(provider, request, (page,))
    assert not provider.requests


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize(
    "matrix",
    [
        (0.0,) * 9,
        (float("inf"), 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
        (2.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
    ],
)
def test_direct_vision_revalidates_supplied_transform(matrix, repair):
    from trade_extract.domain.conditioning import CoordinateTransform

    transform = CoordinateTransform((100, 100), (100, 100))
    # Simulates malformed deserialized metadata bypassing the ordinary constructor.
    object.__setattr__(transform, "forward", matrix)
    page = PageInput(
        0,
        PageImage(0, b"bytes", 100, 100),
        conditioning_status="conditioned",
        conditioning_version=VERSION,
        transform=transform,
    )
    request = InferenceRequest(
        "commercial_invoice", "a" * 64, {}, "", repair_errors=("bad",) if repair else ()
    )
    provider = MockProvider()
    with pytest.raises(ValueError, match="conditioning_"):
        VisionReader().read(provider, request, (page,))
    assert not provider.requests
