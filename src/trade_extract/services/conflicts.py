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
        for path, field in doc.get("fields", {}).items():
            key = canonical_key(doc["document_type"], path)
            if key is None:
                continue
            reason = None
            try:
                normalized = normalize(key, field["value"])
            except ValueError:
                normalized = None
                reason = "invalid_or_ambiguous_operand"
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
                }
            )
    drafts = []
    for key, sources in sorted(grouped.items()):
        by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for source in sources:
            by_doc[source["document_id"]].append(source)
        if len(by_doc) < 2:
            continue
        if key == "shipment.container_numbers":
            values: list[Any] = [
                frozenset(s["normalized"] for s in group) for group in by_doc.values()
            ]
        elif key.endswith(("_kg", ".package_count")) and all(
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
            and not (key.endswith(("_kg", ".package_count")) and values == [False])
        ):
            continue
        sources.sort(key=lambda s: (s["document_id"], s["field_path"]))
        fingerprint = hashlib.sha256(
            stable_json({"job_id": job_id, "key": key, "sources": sources}).encode()
        ).hexdigest()
        drafts.append(ConflictDraft(key, key.replace("_", " "), fingerprint, sources))
    return drafts
