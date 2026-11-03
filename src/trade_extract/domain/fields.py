from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import Field as PField

from trade_extract.domain.provenance import Provenance, ReaderKind


class ValueType(StrEnum):
    """How a client parses the stated characters; never a platform coercion."""

    STRING = "string"
    DECIMAL = "decimal"
    INTEGER = "integer"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    ENUM = "enum"


class Agreement(StrEnum):
    UNANIMOUS = "unanimous"
    MAJORITY = "majority"
    SINGLE_READER = "single_reader"
    DISPUTED = "disputed"
    HUMAN_CORRECTED = "human_corrected"


class FieldAlternative(BaseModel):
    """One reader's competing value for a disputed field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str
    reader: ReaderKind
    confidence: float | None = PField(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    provenance: Provenance

    @model_validator(mode="after")
    def _reader_evidence_agrees(self) -> FieldAlternative:
        if self.reader is not self.provenance.reader:
            raise ValueError(
                f"alternative reader '{self.reader}' must agree with its provenance reader "
                f"'{self.provenance.reader}'"
            )
        if self.confidence != self.provenance.confidence:
            raise ValueError(
                "alternative confidence must agree with its provenance confidence; "
                f"got {self.confidence} and {self.provenance.confidence}"
            )
        return self


class Field(BaseModel):
    """An emitted leaf: untouched page characters, a declared type and provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["scalar"]
    value: str
    value_type: ValueType
    raw_text: str | None = None
    provenance: Provenance
    agreement: Agreement
    alternatives: list[FieldAlternative] = PField(default_factory=list)

    @model_validator(mode="after")
    def _alternatives_match_agreement(self) -> Field:
        if self.agreement is Agreement.DISPUTED:
            if not self.alternatives:
                raise ValueError(
                    "a disputed field must carry at least one alternative reader value"
                )
        elif self.alternatives:
            raise ValueError(
                "alternatives are present only when agreement is 'disputed'; "
                f"this field declares '{self.agreement}'"
            )
        return self
