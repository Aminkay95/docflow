"""Project retained line-item cells into rows without changing their field paths."""

import re
from typing import Any

from trade_extract.schemas.registry import DOCUMENT_SCHEMAS

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


_CARGO_COLUMNS = (
    ("description", "Description"),
    ("marks", "Marks"),
    ("package_count", "Package count"),
    ("package_type", "Package type"),
    ("net_weight_kg", "Net weight (kg)"),
    ("gross_weight_kg", "Gross weight (kg)"),
    ("volume_m3", "Volume (m³)"),
    ("hs_code", "HS code"),
)


def document_line_items(
    document_type: str, fields: dict[str, Any], tables: dict[str, Any]
) -> dict[str, Any]:
    table_panels = {}
    spec = DOCUMENT_SCHEMAS.get(document_type)
    if spec is not None:
        for table in spec.model.table_specs:
            if table.name == "line_items":
                continue
            rows: dict[int, dict[str, Any]] = {}
            metadata = tables.get(table.name) or {}
            columns = {
                column.key: column.label for column in spec.model.field_set.columns_for(table)
            }
            pattern = re.compile(re.escape(table.name) + r"\[(\d+)\]\.(.+)")
            for path, field in fields.items():
                match = pattern.fullmatch(path)
                if match is None:
                    continue
                index, key = int(match[1]), match[2]
                # Nested arrays remain individual, editable values in their parent row.
                nested = re.fullmatch(r"([a-z_]+)\[(\d+)\]", key)
                base_key = nested[1] if nested else key
                label = columns.get(base_key, base_key.replace("_", " ").capitalize())
                if nested:
                    label += " " + str(int(nested[2]) + 1)
                columns.setdefault(key, label)
                rows.setdefault(index, {})[key] = {"path": path, "field": field}
            if not rows and not metadata:
                continue
            present = {key for cells in rows.values() for key in cells}
            present.update(column["key"] for column in metadata.get("columns") or ())
            for index in metadata.get("unresolved_rows") or ():
                rows.setdefault(index, {})
            stated = metadata.get("row_count_stated")
            table_panels[table.name] = {
                "label": table.name.replace("_", " ").capitalize(),
                "columns": [
                    {"key": key, "label": label} for key, label in columns.items() if key in present
                ],
                "rows": [{"index": index, "cells": cells} for index, cells in sorted(rows.items())],
                "incomplete": bool(
                    metadata.get("truncated")
                    or metadata.get("unresolved_rows")
                    or isinstance(stated, int)
                    and stated > len(rows)
                ),
                "stated_count": stated,
            }
    table_paths = {
        cell["path"]
        for panel in table_panels.values()
        for row in panel["rows"]
        for cell in row["cells"].values()
    }
    if table_paths:
        fields = {path: value for path, value in fields.items() if path not in table_paths}
    if document_type == "bill_of_lading":
        core_columns = _CARGO_COLUMNS[:1]
        optional_columns = _CARGO_COLUMNS[1:]
    elif document_type in ("commercial_invoice", "proforma_invoice"):
        core_columns = _CORE_COLUMNS
        optional_columns = _OPTIONAL_COLUMNS
    else:
        return {
            "scalar_fields": fields,
            "line_item_table": None,
            "table_panels": table_panels,
        }
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
    columns = list(core_columns)
    columns.extend(column for column in optional_columns if column[0] in present)
    known = {key for key, _ in (*core_columns, *optional_columns)}
    columns.extend((key, key.replace("_", " ").title()) for key in sorted(present - known))
    rows = [{"index": index, "cells": cells} for index, cells in sorted(grouped.items())]
    metadata = tables.get("line_items") or {}
    stated = metadata.get("row_count_stated")
    incomplete = bool(
        metadata.get("truncated")
        or metadata.get("unresolved_rows")
        or (isinstance(stated, int) and stated > len(rows))
    )
    line_item_table = {
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": rows,
        "incomplete": incomplete,
        "stated_count": stated,
        "label": "Line items",
    }
    return {
        "scalar_fields": scalars,
        "line_item_table": line_item_table,
        "table_panels": {"items": line_item_table, **table_panels},
    }
