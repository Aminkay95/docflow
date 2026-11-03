"""Proforma invoice: the invoice field set renamed and with B/L identity removed."""

from __future__ import annotations

from dataclasses import replace
from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, FieldSpec, TableSpec
from trade_extract.schemas.common import field
from trade_extract.schemas.invoice import INVOICE_FIELD_SET

_BL_NUMBER = "bill_of_lading.number"


def _proforma_specs() -> tuple[FieldSpec, ...]:
    specs: list[FieldSpec] = []
    for spec in INVOICE_FIELD_SET.specs:
        if spec.path == _BL_NUMBER:
            # A proforma is issued before shipment, so it never carries a B/L number.
            continue
        path = spec.path
        if path.startswith("invoice."):
            path = "proforma." + path.removeprefix("invoice.")
        specs.append(replace(spec, path=path))
    specs.append(field("proforma.valid_until", ValueType.DATE))
    return tuple(specs)


PROFORMA_FIELD_SET = FieldSet(specs=_proforma_specs())

__all__ = ["ProformaInvoice", "PROFORMA_FIELD_SET"]


class ProformaInvoice(DocumentEnvelope):
    """Full review; shares the invoice structure without B/L identity."""

    field_set: ClassVar[FieldSet] = PROFORMA_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = (TableSpec("line_items"),)

    document_type: Literal["proforma_invoice"] = "proforma_invoice"
    schema_version: Literal["proforma.peppol-1.0"] = "proforma.peppol-1.0"
