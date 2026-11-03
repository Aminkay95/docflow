"""Canonical comparison over one caller-asserted job, with every source retained."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal
from typing import Any

from trade_extract.domain.canonical import ConflictDraft, canonical_key, normalize
from trade_extract.domain.hashing import stable_json
from trade_extract.domain.numbers import exact_arithmetic


@exact_arithmetic
def detect_conflicts(
    job_id: str, documents: list[dict[str, Any]], *, tolerance: Decimal = Decimal("0.01")
) -> list[ConflictDraft]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in documents:
        fields = {**doc.get("fields", {}), **doc.get("extension", {})}
        for path, field in fields.items():
            key = canonical_key(doc["document_type"], path)
            # Printed components compare only against explicitly matching semantics.
            # No sum, customs valuation, exchange conversion or inferred charge labels.
            if doc["document_type"] == "custom_entry":
                key = {
                    "consignment.fob_value": "customs.fob_amount",
                    "consignment.cif_value": "customs.cif_amount",
                    "consignment.freight_value": "customs.freight_amount",
                    "consignment.insurance_value": "customs.insurance_amount",
                }.get(path, key)
            elif doc["document_type"] == "commercial_invoice":
                term = fields.get("incoterm.code", {}).get("value", "").strip().upper()
                if path == "totals.payable_amount" and term in ("FOB", "CIF"):
                    key = f"customs.{term.lower()}_amount"
                key = {
                    "extension.any.freight_amount": "customs.freight_amount",
                    "extension.any.insurance_amount": "customs.insurance_amount",
                }.get(path, key)
            if key is None:
                continue
            reason = None
            try:
                normalized = normalize(key, field["value"])
            except ValueError:
                normalized = None
                reason = "invalid_or_ambiguous_operand"
            currency = None
            if key.endswith("_amount"):
                currency = next(
                    (
                        fields[p]["value"].strip().upper()
                        for p in (
                            "invoice.currency",
                            "proforma.currency",
                            "consignment.invoice_currency",
                        )
                        if p in fields
                    ),
                    None,
                )
                if currency is None:
                    normalized = None
                    reason = "incomparable_missing_currency"
            # Explicitly contradictory unit metadata cannot be compared as kg.
            if key.endswith("_kg"):
                unit_path = key.removesuffix("_kg") + "_unit"
                unit = doc.get("fields", {}).get(unit_path)
                if unit and unit["value"].strip().upper() not in (
                    "KG",
                    "KGM",
                    "KILOGRAM",
                    "KILOGRAMS",
                ):
                    normalized = None
                    reason = "incomparable_unit"
            grouped[key].append(
                {
                    "document_id": doc["document_id"],
                    "document_type": doc["document_type"],
                    "field_path": path,
                    "stated_value": field["value"],
                    "provenance": field["provenance"],
                    "normalized": str(normalized) if normalized is not None else None,
                    "comparison_reason": reason,
                    **({"currency": currency} if key.endswith("_amount") else {}),
                }
            )
    drafts = []
    for key, sources in sorted(grouped.items()):
        if key.startswith("customs.") and {s["document_type"] for s in sources} != {
            "commercial_invoice", "custom_entry"
        }:
            continue
        by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source in sources:
            by_doc[source["document_id"]].append(source)
        if len(by_doc) < 2:
            continue
        if key.endswith("_amount") and len({s.get("currency") for s in sources}) > 1:
            for source in sources:
                source["comparison_reason"] = "incomparable_currency"
                source["normalized"] = None
        if key == "shipment.container_numbers":
            values: list[Any] = [
                frozenset(s["normalized"] for s in group) for group in by_doc.values()
            ]
        elif key.endswith(("_kg", ".package_count", "_amount")) and all(
            s["normalized"] is not None for s in sources
        ):
            amounts = [Decimal(s["normalized"]) for s in sources]
            values = [max(amounts) - min(amounts) <= tolerance]
            if values[0]:
                continue
        else:
            values = [s["normalized"] for s in sources]
        incomparable = any(s["comparison_reason"] for s in sources)
        if (
            not incomparable
            and len(set(values)) <= 1
            and not (key.endswith(("_kg", ".package_count", "_amount")) and values == [False])
        ):
            continue
        sources.sort(key=lambda s: (s["document_id"], s["field_path"]))
        fingerprint = hashlib.sha256(
            stable_json({"job_id": job_id, "key": key, "sources": sources}).encode()
        ).hexdigest()
        drafts.append(ConflictDraft(key, key.replace("_", " "), fingerprint, sources))
    return drafts
