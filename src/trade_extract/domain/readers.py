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
