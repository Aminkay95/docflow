"""Bill of lading, following the DCSA eBL structure."""

from __future__ import annotations

from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, TableSpec
from trade_extract.schemas.common import container_specs, field, party_specs

BL_FIELD_SET = FieldSet(
    specs=(
        field("bl.number", required=True),
        field("bl.form", ValueType.ENUM),
        field("bl.issue_date", ValueType.DATE, required=True),
        field("bl.place_of_issue"),
        field("bl.original_count", ValueType.INTEGER),
        field("bl.terms"),
        field("carrier.name", required=True),
        field("carrier.scac"),
        *party_specs("shipper", name_required=True),
        *party_specs("consignee", name_required=True),
        field("notify_party.name"),
        field("notify_party.address.line1"),
        field("booking.number"),
        field("service.contract"),
        field("vessel.name"),
        field("vessel.imo"),
        field("voyage.number"),
        field("routing.port_of_loading", required=True),
        field("routing.port_of_discharge", required=True),
        field("routing.place_of_receipt"),
        field("routing.place_of_delivery"),
        field("dates.shipped_on_board", ValueType.DATE),
        field("dates.arrival_estimated", ValueType.DATE),
        field("freight.terms", ValueType.ENUM),
        field("freight.amount", ValueType.DECIMAL),
        field("freight.currency", ValueType.ENUM),
        *container_specs(
            "containers",
            (
                "number",
                "seal",
                "type",
                "package_count",
                "package_type",
                "gross_weight_kg",
                "net_weight_kg",
                "volume_m3",
                "marks",
                "description",
                "hs_code",
            ),
        ),
        field("clauses[].text"),
        field("signature.place"),
        field("signature.date", ValueType.DATE),
        field("signature.signed_by"),
        field("agent.name"),
    )
)

__all__ = ["BL_FIELD_SET", "BillOfLading"]


class BillOfLading(DocumentEnvelope):
    """Highest layout variance and least arithmetic in the set."""

    field_set: ClassVar[FieldSet] = BL_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = (TableSpec("containers"),)

    document_type: Literal["bill_of_lading"] = "bill_of_lading"
    schema_version: Literal["bl.dcsa-1.0"] = "bl.dcsa-1.0"
