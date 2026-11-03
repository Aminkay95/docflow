"""Verification memo, an internal CFS or client-supplied free-form record."""

from __future__ import annotations

from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, TableSpec
from trade_extract.schemas.common import field

VERIFICATION_MEMO_FIELD_SET = FieldSet(
    specs=(
        field("memo.number"),
        field("memo.date", ValueType.DATE, required=True),
        field("memo.type", ValueType.ENUM),
        field("cfs.name"),
        field("cfs.location"),
        field("container.number"),
        field("container.seal"),
        field("bill_of_lading.number"),
        field("delivery_order.number"),
        field("packages.stated", ValueType.INTEGER),
        field("packages.received", ValueType.INTEGER),
        field("packages.damaged", ValueType.INTEGER),
        field("packages.short", ValueType.INTEGER),
        field("packages.over", ValueType.INTEGER),
        field("condition.seal_intact", ValueType.BOOLEAN),
        field("condition.cargo"),
        field("findings[].text"),
        field("officer.name"),
        field("officer.signature"),
        field("remarks"),
    )
)

__all__ = ["VERIFICATION_MEMO_FIELD_SET", "VerificationMemo"]


class VerificationMemo(DocumentEnvelope):
    """Full review; near-free-form content, with unnamed detail welcome under extension."""

    field_set: ClassVar[FieldSet] = VERIFICATION_MEMO_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = (TableSpec("findings"),)

    document_type: Literal["verification_memo"] = "verification_memo"
    schema_version: Literal["verification_memo.local-1.0"] = "verification_memo.local-1.0"
