"""Custom entry, provisionally derived from EAC import-declaration practice.

The field set is an educated guess pending confirmation from a working clearing agent. It is
implemented as data, not validated truth. Confirm every field against a live iCMS declaration
before treating any of it as contractual.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, TableSpec
from trade_extract.schemas.common import field

PROVISIONAL = True
PROVISIONAL_REASON = (
    "WCO-derived first pass pending confirmation from a working clearing agent and a live "
    "iCMS declaration; the paths, requiredness and value types may change in a later version."
)

CUSTOM_ENTRY_FIELD_SET = FieldSet(
    specs=(
        # Declaration.
        field("declaration.number", required=True),
        field("declaration.type", ValueType.ENUM),
        field("declaration.registration_date", ValueType.DATE, required=True),
        field("declaration.customs_office"),
        field("declarant.agent_name"),
        field("declarant.license"),
        field("importer.name", required=True),
        field("importer.pin"),
        field("importer.address.line1"),
        # Consignment.
        field("consignment.bl_number"),
        field("consignment.awb_number"),
        field("consignment.vessel"),
        field("consignment.flight"),
        field("consignment.voyage"),
        field("consignment.arrival_date", ValueType.DATE),
        field("consignment.port_of_loading"),
        field("consignment.port_of_discharge"),
        field("consignment.country_of_origin", ValueType.ENUM),
        field("consignment.country_of_export", ValueType.ENUM),
        field("consignment.incoterm", ValueType.ENUM),
        field("consignment.invoice_number"),
        field("consignment.invoice_date", ValueType.DATE),
        field("consignment.invoice_currency", ValueType.ENUM),
        field("consignment.exchange_rate", ValueType.DECIMAL),
        field("consignment.fob_value", ValueType.DECIMAL),
        field("consignment.freight_value", ValueType.DECIMAL),
        field("consignment.insurance_value", ValueType.DECIMAL),
        field("consignment.cif_value", ValueType.DECIMAL),
        # Goods lines.
        field("goods_lines[].item_number"),
        field("goods_lines[].hs_code"),
        field("goods_lines[].description", required=True),
        field("goods_lines[].quantity", ValueType.DECIMAL),
        field("goods_lines[].unit_of_measure", ValueType.ENUM),
        field("goods_lines[].gross_weight_kg", ValueType.DECIMAL),
        field("goods_lines[].net_weight_kg", ValueType.DECIMAL),
        field("goods_lines[].package_count", ValueType.INTEGER),
        field("goods_lines[].package_type", ValueType.ENUM),
        field("goods_lines[].marks_and_numbers"),
        field("goods_lines[].container_numbers[]"),
        field("goods_lines[].seal_numbers[]"),
        # Assessment: every figure is read as stated, never computed.
        field("assessment[].line_value", ValueType.DECIMAL),
        field("assessment[].customs_value", ValueType.DECIMAL),
        field("assessment[].duty_rate", ValueType.DECIMAL),
        field("assessment[].duty_amount", ValueType.DECIMAL),
        field("assessment[].import_declaration_fee", ValueType.DECIMAL),
        field("assessment[].railway_development_levy", ValueType.DECIMAL),
        field("assessment[].excise", ValueType.DECIMAL),
        field("assessment[].vat", ValueType.DECIMAL),
        field("assessment[].total_payable", ValueType.DECIMAL),
        # Permits.
        field("permits.idf_reference"),
        field("permits.pvoc_reference"),
        field("permits.import_permits[]"),
        field("permits.certificate_of_origin"),
        field("permits.preferential_treatment"),
    )
)

__all__ = ["CUSTOM_ENTRY_FIELD_SET", "PROVISIONAL", "PROVISIONAL_REASON", "CustomEntry"]


class CustomEntry(DocumentEnvelope):
    """Full review; the field set is provisional and must say so everywhere it is exposed."""

    field_set: ClassVar[FieldSet] = CUSTOM_ENTRY_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = (
        TableSpec("goods_lines"),
        TableSpec("assessment"),
    )

    document_type: Literal["custom_entry"] = "custom_entry"
    schema_version: Literal["custom_entry.wco-1.0"] = "custom_entry.wco-1.0"
