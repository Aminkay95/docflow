"""Reader evidence and candidate contracts."""

from __future__ import annotations

from dataclasses import dataclass

from trade_extract.domain.conditioning import VERSION, CoordinateTransform
from trade_extract.domain.fields import Field
from trade_extract.domain.inference import PageImage
from trade_extract.domain.provenance import BBox, ReaderKind


@dataclass(frozen=True)
class TextCharacter:
    text: str
    bbox: BBox


@dataclass(frozen=True)
class PageInput:
    page_index: int
    image: PageImage | None = None
    characters: tuple[TextCharacter, ...] = ()
    unreadable: bool = False
    conditioning_version: str | None = None
    conditioning_status: str = "pending"
    unreadable_reasons: tuple[str, ...] = ()
    transform: CoordinateTransform | None = None
    features: PageFeatures | None = None
    ocr: OCREvidence | None = None

    @property
    def vision_ready(self) -> bool:
        if self.transform is not None:
            self.transform.validate()
            if self.image is not None and self.transform.conditioned_size != (
                self.image.width_px,
                self.image.height_px,
            ):
                raise ValueError("conditioning_image_dimensions_mismatch")
        return (
            not self.unreadable
            and self.conditioning_status == "conditioned"
            and self.conditioning_version == VERSION
            and self.transform is not None
        )

    @property
    def is_unreadable(self) -> bool:
        return self.unreadable or self.conditioning_status == "unreadable"

    @property
    def text(self) -> str:
        return "".join(character.text for character in self.characters)


@dataclass(frozen=True)
class FieldCandidate:
    field_path: str
    field: Field

    @property
    def reader(self) -> ReaderKind:
        return self.field.provenance.reader


@dataclass(frozen=True)
class PageFeatures:
    good_text: bool | None = None
    flat_scan: bool | None = None
    photograph: bool | None = None
    skew: bool | None = None
    stamps: bool | None = None
    handwriting: bool | None = None
    dense_table: bool | None = None


@dataclass(frozen=True)
class OCRSpan:
    text: str
    bbox: BBox
    confidence: float | None = None


@dataclass(frozen=True)
class TableRow:
    bbox: BBox
    cells: tuple[BBox, ...] = ()
    header: bool = False


@dataclass(frozen=True)
class TableRegion:
    region_id: str
    bbox: BBox
    rows: tuple[TableRow, ...]
    table_name: str | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class OCREvidence:
    page_index: int
    status: str
    spans: tuple[OCRSpan, ...] = ()
    tables: tuple[TableRegion, ...] = ()
    diagnostics: tuple[str, ...] = ()
    version: str = "paddle-structure-v1"
    structure_status: str = "ok"


@dataclass(frozen=True)
class PageRoute:
    page_index: int
    readers: tuple[ReaderKind, ...]
    primary: ReaderKind | None
    reasons: tuple[str, ...]
    table_required: bool
    policy: tuple[tuple[str, str | int | float | bool], ...]
    availability: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class InventoryRow:
    table_name: str
    row_index: int
    page_index: int
    region_id: str
    bbox: BBox


@dataclass(frozen=True)
class TableInventory:
    rows: tuple[InventoryRow, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeCrop:
    image: PageImage
    rectangle: tuple[int, int, int, int]
    page_size: tuple[int, int]
    row: InventoryRow

    def page_box(self, bbox: BBox) -> BBox:
        x, y, right, bottom = self.rectangle
        width, height = self.page_size
        return BBox(
            x0=(x + bbox.x0 * (right - x)) / width,
            y0=(y + bbox.y0 * (bottom - y)) / height,
            x1=(x + bbox.x1 * (right - x)) / width,
            y1=(y + bbox.y1 * (bottom - y)) / height,
        )
