"""Delivery order, a proprietary extension block issued against the bill of lading."""

from __future__ import annotations

from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, TableSpec
from trade_extract.schemas.common import container_specs, field

DELIVERY_ORDER_FIELD_SET = FieldSet(
    specs=(
        field("delivery_order.number", required=True),
        field("delivery_order.issue_date", ValueType.DATE, required=True),
        field("delivery_order.valid_until", ValueType.DATE),
        field("bill_of_lading.number", required=True),
        field("carrier.name"),
        field("agent.name"),
        field("consignee.name", required=True),
        field("consignee.address.line1"),
        field("notify_party.name"),
        field("release.terminal"),
        field("release.warehouse"),
        field("release.reference"),
        field("release.free_time_days", ValueType.INTEGER),
        *container_specs(
            "containers",
            (
                "number",
                "seal",
                "type",
                "gross_weight_kg",
                "package_count",
                "package_type",
            ),
        ),
        field("cargo.description"),
        field("cargo.marks"),
        field("charges[].description"),
        field("charges[].amount", ValueType.DECIMAL),
        field("charges[].currency", ValueType.ENUM),
        field("instructions"),
        field("signature.name"),
    )
)

__all__ = ["DELIVERY_ORDER_FIELD_SET", "DeliveryOrder"]


class DeliveryOrder(DocumentEnvelope):
    """Full review; the B/L number is the cross-document anchor."""

    field_set: ClassVar[FieldSet] = DELIVERY_ORDER_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = (TableSpec("containers"),)

    document_type: Literal["delivery_order"] = "delivery_order"
    schema_version: Literal["delivery_order.local-1.0"] = "delivery_order.local-1.0"
