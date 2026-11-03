"""Explicit shared keys; normalization never edits stated evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from trade_extract.domain.numbers import decimal_value

# No inferred shipment grouping or fuzzy matching. Only these asserted semantics compare.
SHARED = frozenset(
    (
        "bill_of_lading.number",
        "shipment.gross_weight_kg",
        "shipment.net_weight_kg",
        "shipment.package_count",
        "shipment.vessel",
        "shipment.voyage",
        "shipment.port_of_loading",
        "shipment.port_of_discharge",
        "shipment.country_of_origin",
        "invoice.number",
        "invoice.currency",
    )
)

ALIASES = {
    "bill_of_lading": {
        "bl.number": "bill_of_lading.number",
        "vessel.name": "shipment.vessel",
        "voyage.number": "shipment.voyage",
        "routing.port_of_loading": "shipment.port_of_loading",
        "routing.port_of_discharge": "shipment.port_of_discharge",
    },
    "custom_entry": {
        "consignment.bl_number": "bill_of_lading.number",
        "consignment.invoice_number": "invoice.number",
        "consignment.invoice_currency": "invoice.currency",
        "consignment.vessel": "shipment.vessel",
        "consignment.voyage": "shipment.voyage",
        "consignment.port_of_loading": "shipment.port_of_loading",
        "consignment.port_of_discharge": "shipment.port_of_discharge",
        "consignment.country_of_origin": "shipment.country_of_origin",
    },
}


def canonical_key(document_type: str, path: str) -> str | None:
    if path in ALIASES.get(document_type, {}):
        return ALIASES[document_type][path]
    if path in SHARED:
        return path
    if re.fullmatch(r"shipment.container_numbers\[\d+\]|containers\[\d+\].number", path):
        return "shipment.container_numbers"
    return None


def normalize(key: str, value: str) -> str | Decimal:
    if key.endswith(("_kg", ".package_count")):
        return decimal_value(value)
    if key.endswith("_date"):
        return date.fromisoformat(value).isoformat()
    return " ".join(value.split()).casefold()


@dataclass(frozen=True)
class ConflictDraft:
    canonical_key: str
    field_label: str
    fingerprint: str
    sources: list[dict[str, Any]]
