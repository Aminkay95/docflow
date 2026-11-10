"""Project retained invoice cells into rows without changing their field paths."""

import re
from typing import Any

_ITEM_PATH = re.compile(r"^line_items\[(\d+)\]\.([a-z_]+)$")
_CORE_COLUMNS = (
    ("description", "Description"),
    ("quantity", "Quantity"),
    ("unit_price", "Unit price"),
    ("line_extension_amount", "Amount"),
)
_OPTIONAL_COLUMNS = (
    ("unit_code", "Unit"),
    ("line_number", "Line number"),
    ("hs_code", "HS code"),
    ("tax_category", "Tax category"),
    ("tax_rate", "Tax rate"),
    ("tax_amount", "Tax amount"),
    ("allowance_charge", "Allowance / charge"),
    ("country_of_origin", "Country of origin"),
    ("note", "Note"),
)


def invoice_line_items(
    document_type: str, fields: dict[str, Any], tables: dict[str, Any]
) -> dict[str, Any]:
    if document_type not in ("commercial_invoice", "proforma_invoice"):
        return {"scalar_fields": fields, "line_item_table": None}
    scalars: dict[str, Any] = {}
    grouped: dict[int, dict[str, Any]] = {}
    present: set[str] = set()
    for path, field in fields.items():
        match = _ITEM_PATH.fullmatch(path)
        if match is None:
            scalars[path] = field
            continue
        index, key = int(match[1]), match[2]
        grouped.setdefault(index, {})[key] = {"path": path, "field": field}
        present.add(key)
    columns = list(_CORE_COLUMNS)
    columns.extend(column for column in _OPTIONAL_COLUMNS if column[0] in present)
    known = {key for key, _ in (*_CORE_COLUMNS, *_OPTIONAL_COLUMNS)}
    columns.extend((key, key.replace("_", " ").title()) for key in sorted(present - known))
    rows = [{"index": index, "cells": cells} for index, cells in sorted(grouped.items())]
    metadata = tables.get("line_items") or {}
    stated = metadata.get("row_count_stated")
    incomplete = bool(
        metadata.get("truncated")
        or metadata.get("unresolved_rows")
        or (isinstance(stated, int) and stated > len(rows))
    )
    return {
        "scalar_fields": scalars,
        "line_item_table": {
            "columns": [{"key": key, "label": label} for key, label in columns],
            "rows": rows,
            "incomplete": incomplete,
            "stated_count": stated,
        },
    }
