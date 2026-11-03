"""Commercial invoice field set, following UBL 2.1 / Peppol BIS Billing 3.0 semantics."""

from __future__ import annotations

from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, TableSpec
from trade_extract.schemas.common import field, party_specs, shipment_specs

INVOICE_FIELD_SET = FieldSet(
    specs=(
        # Parties and references.
        field("invoice.number", required=True),
        field("invoice.issue_date", ValueType.DATE, required=True),
        field("invoice.due_date", ValueType.DATE),
        field("invoice.type_code", ValueType.ENUM),
        field("invoice.currency", ValueType.ENUM, required=True),
        field("invoice.tax_point_date", ValueType.DATE),
        field("invoice.note"),
        *party_specs("seller", name_required=True, with_identifier=True),
        *party_specs("buyer", name_required=True, with_identifier=True),
        *party_specs("ship_to", name_required=False),
        field("order.reference"),
        field("despatch_advice.reference"),
        field("receiving_advice.reference"),
        field("bill_of_lading.number"),
        field("payment.terms"),
        field("payment.means_code", ValueType.ENUM),
        field("payment.iban"),
        field("incoterm.code", ValueType.ENUM),
        field("incoterm.location"),
        # Shipment.
        *shipment_specs(),
        # Line items: one row per source row, every cell carrying its own provenance.
        field("line_items[].line_number"),
        field("line_items[].description", required=True),
        field("line_items[].quantity", ValueType.DECIMAL, required=True),
        field("line_items[].unit_code", ValueType.ENUM),
        field("line_items[].unit_price", ValueType.DECIMAL, required=True),
        field("line_items[].line_extension_amount", ValueType.DECIMAL, required=True),
        field("line_items[].hs_code"),
        field("line_items[].tax_category", ValueType.ENUM),
        field("line_items[].tax_rate", ValueType.DECIMAL),
        field("line_items[].tax_amount", ValueType.DECIMAL),
        field("line_items[].allowance_charge", ValueType.DECIMAL),
        field("line_items[].country_of_origin", ValueType.ENUM),
        field("line_items[].note"),
        # Totals and tax.
        field("totals.line_extension_amount", ValueType.DECIMAL, required=True),
        field("totals.tax_exclusive_amount", ValueType.DECIMAL, required=True),
        field("totals.tax_amount", ValueType.DECIMAL),
        field("totals.tax_inclusive_amount", ValueType.DECIMAL, required=True),
        field("totals.allowance_total", ValueType.DECIMAL),
        field("totals.charge_total", ValueType.DECIMAL),
        field("totals.prepaid_amount", ValueType.DECIMAL),
        field("totals.payable_amount", ValueType.DECIMAL, required=True),
        field("totals.amount_in_words"),
        field("tax_breakdown[].taxable_amount", ValueType.DECIMAL),
        field("tax_breakdown[].tax_amount", ValueType.DECIMAL),
        field("tax_breakdown[].tax_category", ValueType.ENUM),
        field("tax_breakdown[].tax_rate", ValueType.DECIMAL),
    )
)

__all__ = ["CommercialInvoice", "INVOICE_FIELD_SET"]


class CommercialInvoice(DocumentEnvelope):
    """The only document type that can pass without human review in v1."""

    field_set: ClassVar[FieldSet] = INVOICE_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = (TableSpec("line_items"),)

    document_type: Literal["commercial_invoice"] = "commercial_invoice"
    schema_version: Literal["invoice.peppol-1.0"] = "invoice.peppol-1.0"
