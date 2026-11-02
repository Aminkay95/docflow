from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256_RE = re.compile(r"(?:sha256:)?([0-9A-Fa-f]{64})")


def bare_sha256(value: str) -> str:
    """Return the bare digest for comparisons without rewriting the supplied value."""

    match = _SHA256_RE.fullmatch(value)
    if match is None:
        raise ValueError(
            "must be a 64-character SHA-256 hex digest, optionally prefixed with 'sha256:'"
        )
    return match.group(1).lower()


class CoordinateSpace(StrEnum):
    """How a bounding box maps onto the rendered page."""

    NORMALIZED_TOP_LEFT = "normalized_top_left"


class ReaderKind(StrEnum):
    TEXT_LAYER = "text_layer"
    PADDLE = "paddle"
    VISION = "vision"
    HUMAN = "human"


class BBox(BaseModel):
    """A normalised page rectangle, origin top-left, with every coordinate in 0–1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x0: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y0: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    x1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.x0 > self.x1:
            raise ValueError(f"bbox x0 ({self.x0}) must be less than or equal to x1 ({self.x1})")
        if self.y0 > self.y1:
            raise ValueError(f"bbox y0 ({self.y0}) must be less than or equal to y1 ({self.y1})")
        return self

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class Provenance(BaseModel):
    """The page, rectangle, reader and segment identity behind one emitted leaf."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_index: int = Field(ge=0)
    page_number: int | None = Field(default=None, ge=1)
    bbox: BBox
    coordinate_space: CoordinateSpace
    page_width_pt: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    page_height_pt: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    reader: ReaderKind
    reader_version: str | None = None
    model: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    document_hash: str

    @model_validator(mode="after")
    def _consistent(self) -> Provenance:
        if self.page_number is not None and self.page_number != self.page_index + 1:
            raise ValueError(
                f"page_number {self.page_number} does not match page_index {self.page_index}; "
                "page_number is page_index + 1"
            )
        bare_sha256(self.document_hash)
        return self

    @property
    def document_hash_hex(self) -> str:
        return bare_sha256(self.document_hash)

    def matches(self, document_hash: str) -> bool:
        """Return whether this provenance names the same segment hash."""

        try:
            return self.document_hash_hex == bare_sha256(document_hash)
        except ValueError:
            return False

    def within(self, page_start: int, page_end: int) -> bool:
        """Return whether ``page_index`` is inside the inclusive source page range."""

        if page_start < 0 or page_end < page_start:
            raise ValueError(f"invalid page range {page_start}..{page_end}")
        return page_start <= self.page_index <= page_end
