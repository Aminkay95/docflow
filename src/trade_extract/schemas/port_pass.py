"""Port pass, supplied by the caller; the platform never fetches it from a terminal."""

from __future__ import annotations

from typing import ClassVar, Literal

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import DocumentEnvelope, FieldSet, TableSpec
from trade_extract.schemas.common import field

PORT_PASS_FIELD_SET = FieldSet(
    specs=(
        field("port_pass.number", required=True),
        field("port_pass.issue_date", ValueType.DATE, required=True),
        field("port_pass.valid_from", ValueType.DATETIME),
        field("port_pass.valid_to", ValueType.DATETIME),
        field("port.name"),
        field("port.terminal"),
        field("port.gate"),
        field("vehicle.plate"),
        field("vehicle.type"),
        field("driver.name"),
        field("driver.license"),
        field("transporter.name"),
        field("container.number"),
        field("container.seal"),
        field("container.size_type"),
        field("container.gross_weight_kg", ValueType.DECIMAL),
        field("cargo.description"),
        field("release.reference"),
        field("bill_of_lading.number"),
        field("delivery_order.number"),
        field("movement.direction", ValueType.ENUM),
        field("escort.required", ValueType.BOOLEAN),
        field("remarks"),
    )
)

__all__ = ["PORT_PASS_FIELD_SET", "PortPass"]


class PortPass(DocumentEnvelope):
    """Full review; validity window and direction are advisory checks only."""

    field_set: ClassVar[FieldSet] = PORT_PASS_FIELD_SET
    table_specs: ClassVar[tuple[TableSpec, ...]] = ()

    document_type: Literal["port_pass"] = "port_pass"
    schema_version: Literal["port_pass.local-1.0"] = "port_pass.local-1.0"
