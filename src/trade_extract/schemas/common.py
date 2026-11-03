"""Reusable field specs for parties, addresses, shipment detail and containers."""

from __future__ import annotations

from trade_extract.domain.fields import ValueType
from trade_extract.schemas.base import FieldSpec

ValueTypeLike = ValueType | str

_CONTAINER_TYPES: dict[str, ValueType] = {
    "number": ValueType.STRING,
    "seal": ValueType.STRING,
    "type": ValueType.STRING,
    "package_count": ValueType.INTEGER,
    "package_type": ValueType.ENUM,
    "gross_weight_kg": ValueType.DECIMAL,
    "net_weight_kg": ValueType.DECIMAL,
    "volume_m3": ValueType.DECIMAL,
    "marks": ValueType.STRING,
    "description": ValueType.STRING,
    "hs_code": ValueType.STRING,
}

_CONTAINER_LABELS: dict[str, str] = {
    "number": "Container",
    "seal": "Seal",
    "type": "Type",
    "package_count": "Packages",
    "package_type": "Package type",
    "gross_weight_kg": "Gross kg",
    "net_weight_kg": "Net kg",
    "volume_m3": "Volume m³",
    "marks": "Marks",
    "description": "Description",
    "hs_code": "HS",
}


def field(
    path: str,
    value_type: ValueTypeLike = ValueType.STRING,
    *,
    required: bool = False,
    label: str | None = None,
) -> FieldSpec:
    """Build one spec, accepting either a ``ValueType`` or its string value."""

    return FieldSpec(
        path=path,
        value_type=ValueType(value_type),
        required=required,
        label=label,
    )


def address_specs(prefix: str) -> tuple[FieldSpec, ...]:
    """Street line, city and country beneath one party prefix."""

    return (
        field(f"{prefix}.line1"),
        field(f"{prefix}.city"),
        field(f"{prefix}.country", ValueType.ENUM),
    )


def party_specs(
    prefix: str,
    *,
    name_required: bool = True,
    with_identifier: bool = False,
) -> tuple[FieldSpec, ...]:
    """A named party, optionally with its tax/PIN identifier and address."""

    specs: list[FieldSpec] = [field(f"{prefix}.name", required=name_required)]
    if with_identifier:
        specs.append(field(f"{prefix}.identifier"))
    specs.extend(address_specs(f"{prefix}.address"))
    return tuple(specs)


def shipment_specs() -> tuple[FieldSpec, ...]:
    """The invoice-family shipment block."""

    return (
        field("shipment.gross_weight_kg", ValueType.DECIMAL),
        field("shipment.gross_weight_unit", ValueType.ENUM),
        field("shipment.net_weight_kg", ValueType.DECIMAL),
        field("shipment.net_weight_unit", ValueType.ENUM),
        field("shipment.package_count", ValueType.INTEGER),
        field("shipment.package_type", ValueType.ENUM),
        field("shipment.container_numbers[]"),
        field("shipment.seal_numbers[]"),
        field("shipment.vessel"),
        field("shipment.voyage"),
        field("shipment.port_of_loading"),
        field("shipment.port_of_discharge"),
        field("shipment.country_of_origin", ValueType.ENUM),
        field("shipment.marks_and_numbers"),
    )


def container_specs(prefix: str, keys: tuple[str, ...]) -> tuple[FieldSpec, ...]:
    """Indexed container cells for a declared ``containers`` table."""

    specs: list[FieldSpec] = []
    for key in keys:
        try:
            value_type = _CONTAINER_TYPES[key]
        except KeyError:  # pragma: no cover - a developer error, not runtime input
            raise ValueError(f"unknown container key '{key}'") from None
        specs.append(
            field(
                f"{prefix}[].{key}",
                value_type,
                label=_CONTAINER_LABELS[key],
            )
        )
    return tuple(specs)


def invoice_container_specs() -> tuple[FieldSpec, ...]:
    """Invoice-family shipment container/seal arrays, which are not a declared table."""

    return (field("shipment.container_numbers[]"), field("shipment.seal_numbers[]"))
