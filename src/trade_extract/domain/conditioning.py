"""Image-free, immutable page-conditioning and projective geometry contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from trade_extract.domain.provenance import BBox


class RefusalReason(StrEnum):
    BLANK = "blank"
    BLURRED = "blurred"
    LOW_CONTRAST = "low_contrast"
    INSUFFICIENT_SOURCE_DETAIL = "insufficient_source_detail"
    EXCESSIVE_DARKNESS = "excessive_darkness"
    EXCESSIVE_SKEW = "excessive_skew"
    AMBIGUOUS_ORIENTATION = "ambiguous_orientation"
    UNSUPPORTED_ORIENTATION = "unsupported_orientation"
    UNCERTAIN_BORDER_CONTENT = "uncertain_border_content"
    UNRESOLVED_SKEW = "unresolved_skew"


VERSION = "image-quality-v1"
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


@dataclass(frozen=True)
class CoordinateTransform:
    """Homography on pixel-edge coordinates; source is the rendered image before conditioning.

    Map all four corners for a box. Axis-aligned enclosing boxes are lossy under
    perspective; point and polygon mappings remain reversible.
    """

    source_size: tuple[int, int]
    conditioned_size: tuple[int, int]
    forward: tuple[float, ...] = IDENTITY
    inverse: tuple[float, ...] = IDENTITY

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        for size in (self.source_size, self.conditioned_size):
            if len(size) != 2 or any(not isinstance(v, int) or v <= 0 for v in size):
                raise ValueError("conditioning_invalid_dimensions")
        for matrix in (self.forward, self.inverse):
            if len(matrix) != 9 or any(not math.isfinite(v) for v in matrix):
                raise ValueError("conditioning_invalid_transform")
            a, b, c, d, e, f, g, h, i = matrix
            if abs(a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)) < 1e-12:
                raise ValueError("conditioning_singular_transform")
        product = [
            sum(self.forward[r * 3 + k] * self.inverse[k * 3 + c] for k in range(3))
            for r in range(3)
            for c in range(3)
        ]
        scale = product[8]
        if abs(scale) < 1e-12 or any(
            not math.isclose(value / scale, IDENTITY[index], abs_tol=1e-7)
            for index, value in enumerate(product)
        ):
            raise ValueError("conditioning_inconsistent_inverse")
        # No projective horizon may cross either image rectangle.
        for matrix, size in (
            (self.forward, self.source_size),
            (self.inverse, self.conditioned_size),
        ):
            denominators = [
                matrix[6] * x + matrix[7] * y + matrix[8]
                for x in (0, size[0])
                for y in (0, size[1])
            ]
            if min(denominators) <= 0 <= max(denominators):
                raise ValueError("conditioning_invalid_transform")

    def point(self, x: float, y: float, *, inverse: bool = False) -> tuple[float, float]:
        m = self.inverse if inverse else self.forward
        z = m[6] * x + m[7] * y + m[8]
        if abs(z) < 1e-12:
            raise ValueError("conditioning_invalid_transform")
        return ((m[0] * x + m[1] * y + m[2]) / z, (m[3] * x + m[4] * y + m[5]) / z)

    def box(self, box: BBox, *, inverse: bool = False) -> BBox:
        src, dst = (
            (self.conditioned_size, self.source_size)
            if inverse
            else (self.source_size, self.conditioned_size)
        )
        points = [
            self.point(x * src[0], y * src[1], inverse=inverse)
            for x in (box.x0, box.x1)
            for y in (box.y0, box.y1)
        ]

        def clip(v: float) -> float:
            return min(1.0, max(0.0, v))

        return BBox(
            x0=clip(min(p[0] for p in points) / dst[0]),
            y0=clip(min(p[1] for p in points) / dst[1]),
            x1=clip(max(p[0] for p in points) / dst[0]),
            y1=clip(max(p[1] for p in points) / dst[1]),
        )


@dataclass(frozen=True)
class QualityMeasurements:
    contrast: float
    laplacian_variance: float
    ink_fraction: float
    source_short_edge: int
    skew_degrees: float


@dataclass(frozen=True)
class ConditioningResult:
    image_bytes: bytes
    thumbnail_bytes: bytes
    transform: CoordinateTransform
    measurements: QualityMeasurements
    score: float
    status: Literal["conditioned", "unreadable"]
    reasons: tuple[str, ...]
    operations: tuple[str, ...]
    policy: tuple[tuple[str, float | int], ...]
    dpi: int
    version: str = VERSION
