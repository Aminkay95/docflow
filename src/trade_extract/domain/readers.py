"""Reader evidence and candidate contracts."""

from __future__ import annotations

from dataclasses import dataclass

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
