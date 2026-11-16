"""Independent semantic row inventory and bounded native-resolution crops."""

from __future__ import annotations

import io
import math
import re
from collections import defaultdict
from collections.abc import Mapping

from trade_extract.config import Settings
from trade_extract.domain.fields import Field
from trade_extract.domain.inference import PageImage
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import (
    InventoryRow,
    NativeCrop,
    PageInput,
    TableInventory,
    TableRegion,
)
from trade_extract.schemas.base import TableMeta
from trade_extract.schemas.registry import SchemaSpec
from trade_extract.services.local_extraction import EvidenceLine
from trade_extract.services.table_layout import DigitalGrid, digital_grids, ocr_table_columns


def build_inventory(
    pages: tuple[PageInput, ...],
    required_pages: set[int],
    settings: Settings,
    schema: SchemaSpec | None = None,
    *,
    digital_layouts: Mapping[int, tuple[DigitalGrid, ...]] | None = None,
) -> TableInventory:
    rows: list[InventoryRow] = []
    errors: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    for page in sorted(pages, key=lambda p: p.page_index):
        e = page.ocr
        structure_ok = e is not None and e.structure_status == "ok" and e.status == "ok"
        regions = [
            (table, *(ocr_table_columns(page, table, schema) if schema else (table.table_name, ())))
            for table in (e.tables if e is not None and structure_ok else ())
        ]
        if schema and not any(name for _, name, _ in regions):
            grids = (
                digital_layouts[page.page_index]
                if digital_layouts is not None and page.page_index in digital_layouts
                else digital_grids(page, schema, settings.ocr_max_rows + 1)
            )
            if grids:
                regions = [
                    (
                        TableRegion(
                            f"digital-{grid.name}",
                            BBox(
                                x0=min(row.bbox.x0 for row in grid.rows),
                                y0=min(row.bbox.y0 for row in grid.rows),
                                x1=max(row.bbox.x1 for row in grid.rows),
                                y1=max(row.bbox.y1 for row in grid.rows),
                            ),
                            grid.rows,
                            grid.name,
                        ),
                        grid.name,
                        grid.columns,
                    )
                    for grid in grids
                ]
        if not regions and page.page_index in required_pages:
            issue = (
                "independent_table_not_detected"
                if structure_ok
                else "independent_structure_unavailable"
            )
            errors.append(f"page:{page.page_index}:{issue}")
        previous: list[TableRegion] = []
        for table, name, keys in sorted(
            regions, key=lambda entry: (entry[0].bbox.y0, entry[0].bbox.x0, entry[0].region_id)
        ):
            if any(
                min(t.bbox.x1, table.bbox.x1) > max(t.bbox.x0, table.bbox.x0)
                and min(t.bbox.y1, table.bbox.y1) > max(t.bbox.y0, table.bbox.y0)
                for t in previous
            ):
                errors.append(f"page:{page.page_index}:overlapping_table_regions")
            previous.append(table)
            errors.extend(f"page:{page.page_index}:{d}" for d in table.diagnostics)
            if name is None:
                errors.append(f"page:{page.page_index}:ambiguous_table_correspondence")
                continue
            previous_bottom = -1.0
            for row in table.rows:
                if row.bbox.y0 < previous_bottom - 1e-5:
                    errors.append(f"page:{page.page_index}:ambiguous_row_order")
                previous_bottom = row.bbox.y1
                if row.header:
                    continue
                if len(rows) >= settings.ocr_max_rows:
                    return TableInventory(tuple(rows), (*errors, "table_row_budget_exhausted"))
                cells = sorted(row.cells, key=lambda box: box.x0)
                columns = (
                    tuple(
                        (key, box) for key, box in zip(keys, cells, strict=True) if key is not None
                    )
                    if len(keys) == len(cells)
                    else ()
                )
                rows.append(
                    InventoryRow(
                        name, counts[name], page.page_index, table.region_id, row.bbox, columns
                    )
                )
                counts[name] += 1
    return TableInventory(tuple(rows), tuple(errors))


def complete_wrapped_descriptions(
    inventory: TableInventory, fields: dict[str, Field], lines: tuple[EvidenceLine, ...]
) -> None:
    """Extend a grounded description only across lines inside the same mapped cell."""
    for row in inventory.rows:
        path = f"{row.table_name}[{row.row_index}].description"
        field = fields.get(path)
        cell = dict(row.columns).get("description")
        if field is None or cell is None or field.provenance.reader == ReaderKind.VISION:
            continue
        parts: list[tuple[float, str, BBox]] = []
        for line in lines:
            if line.page_index != row.page_index or line.reader != field.provenance.reader:
                continue
            if line.bbox.y0 > cell.y1 or line.bbox.y1 < cell.y0:
                continue
            if line.characters:
                indexes = [
                    index
                    for index, char in enumerate(line.characters)
                    if char.text.strip()
                    and cell.x0 <= char.bbox.x0
                    and char.bbox.x1 <= cell.x1
                    and cell.y0 <= char.bbox.y0
                    and char.bbox.y1 <= cell.y1
                ]
                if not indexes:
                    continue
                start, end = indexes[0], indexes[-1] + 1
                box = line.box_for(start, end)
                if box and cell.x0 <= box.x0 <= box.x1 <= cell.x1:
                    text = "".join(char.text for char in line.characters[start:end]).strip()
                    parts.append((box.y0, text, box))
            elif (
                cell.x0 <= line.bbox.x0 <= line.bbox.x1 <= cell.x1
                and cell.y0 <= line.bbox.y0 <= line.bbox.y1 <= cell.y1
            ):
                parts.append((line.bbox.y0, line.text, line.bbox))
        if (
            len(parts) < 2
            or max(part[0] for part in parts) - min(part[0] for part in parts) < 0.003
        ):
            continue
        parts.sort(key=lambda part: (round(part[0], 3), part[2].x0))
        raw = "\n".join(part[1] for part in parts)
        value = " ".join(raw.split())
        prefix = " ".join(field.value.split())
        if not value.casefold().startswith(prefix.casefold() + " "):
            continue
        box = BBox(
            x0=min(part[2].x0 for part in parts),
            y0=min(part[2].y0 for part in parts),
            x1=max(part[2].x1 for part in parts),
            y1=max(part[2].y1 for part in parts),
        )
        fields[path] = field.model_copy(
            update={
                "value": value,
                "raw_text": raw,
                "provenance": field.provenance.model_copy(
                    update={
                        "bbox": box,
                        "reader_version": "table-layout-v1",
                    }
                ),
            }
        )


def mismatched_table_columns(
    inventory: TableInventory, fields: dict[str, Field]
) -> tuple[str, ...]:
    """Reject citations wholly or partly outside a positively mapped cell."""
    rows = {(row.table_name, row.row_index): row for row in inventory.rows}
    invalid = []
    for path, field in fields.items():
        match = re.match(r"([a-z_]+)\[(\d+)\]\.([a-z_][a-z_0-9]*)", path)
        if match is None:
            continue
        name, index, key = match.groups()
        row = rows.get((name, int(index)))
        if row is None or not row.columns:
            continue
        columns = dict(row.columns)
        cell = columns.get(key)
        if cell is None:
            # An explicitly unit-qualified heading and its unqualified counterpart
            # represent distinct schema fields, even when they share a physical column.
            other = key.removesuffix("_kg") if key.endswith("_kg") else key + "_kg"
            if other in columns:
                invalid.append(path)
            continue
        provenance = field.provenance
        box = provenance.bbox
        if provenance.page_index != row.page_index or not (
            cell.x0 - 1e-5 <= box.x0 <= box.x1 <= cell.x1 + 1e-5
            and cell.y0 - 1e-5 <= box.y0 <= box.y1 <= cell.y1 + 1e-5
        ):
            invalid.append(path)
    return tuple(invalid)


def reconcile(
    inventory: TableInventory, fields: dict, tables: dict[str, TableMeta]
) -> tuple[str, ...]:
    errors = [
        *inventory.diagnostics,
        *(
            f"{path}:column_geometry_mismatch"
            for path in mismatched_table_columns(inventory, fields)
        ),
    ]
    names = {r.table_name for r in inventory.rows} | set(tables)
    for name in sorted(names):
        expected = {r.row_index for r in inventory.rows if r.table_name == name}
        emitted = {
            int(m.group(1))
            for path in fields
            if (m := re.match(re.escape(name) + r"\[(\d+)\]", path))
        }
        meta = tables.get(name)
        unresolved = set(meta.unresolved_rows) if meta else set()
        if not expected and meta and meta.row_count_stated:
            errors.append(f"table:{name}:independent_structure_unavailable")
        if emitted - expected or unresolved - expected:
            errors.append(f"table:{name}:row_outside_inventory")
        missing = expected - emitted
        if missing != unresolved:
            errors.append(f"table:{name}:unresolved_rows:" + ",".join(map(str, sorted(missing))))
        if meta is None or meta.row_count_stated != len(expected):
            errors.append(f"table:{name}:independent_row_count_mismatch")
        for path, field in fields.items():
            match = re.match(re.escape(name) + r"\[(\d+)\]", path)
            if match:
                row = next(
                    (
                        r
                        for r in inventory.rows
                        if r.table_name == name and r.row_index == int(match.group(1))
                    ),
                    None,
                )
                if row is not None:
                    p, b = field.provenance, row.bbox
                    if p.page_index != row.page_index or not (
                        b.x0 - 1e-5 <= p.bbox.x0 <= p.bbox.x1 <= b.x1 + 1e-5
                        and b.y0 - 1e-5 <= p.bbox.y0 <= p.bbox.y1 <= b.y1 + 1e-5
                    ):
                        errors.append(f"table:{name}:row_geometry_mismatch:{row.row_index}")
    return tuple(errors)


def native_crops(
    pages: tuple[PageInput, ...], inventory: TableInventory, settings: Settings
) -> tuple[NativeCrop, ...]:
    if len(inventory.rows) > settings.table_max_crops:
        raise ValueError("table_crop_budget_exhausted")
    from PIL import Image

    output = []
    total_pixels = 0
    for page in pages:
        rows = [r for r in inventory.rows if r.page_index == page.page_index]
        if not rows:
            continue
        if page.image is None or not page.vision_ready:
            raise ValueError("table_native_image_unavailable")
        image = page.image
        if image.width_px * image.height_px > settings.ocr_max_pixels:
            raise ValueError("table_image_pixel_budget_exhausted")
        try:
            opened = Image.open(io.BytesIO(image.image_bytes))
        except Exception as exc:
            raise ValueError("table_invalid_image") from exc
        with opened as im:
            if im.size != (image.width_px, image.height_px):
                raise ValueError("table_image_dimensions_mismatch")
            try:
                im.load()
            except Exception as exc:
                raise ValueError("table_invalid_image") from exc
            for row in rows:
                b = row.bbox
                rect = (
                    math.floor(b.x0 * im.width),
                    math.floor(b.y0 * im.height),
                    math.ceil(b.x1 * im.width),
                    math.ceil(b.y1 * im.height),
                )
                if (rect[2] - rect[0]) * (rect[3] - rect[1]) > settings.table_max_crop_pixels:
                    raise ValueError("table_crop_pixel_budget_exhausted")
                total_pixels += (rect[2] - rect[0]) * (rect[3] - rect[1])
                if total_pixels > settings.table_max_total_crop_pixels:
                    raise ValueError("table_total_crop_pixel_budget_exhausted")
                out = io.BytesIO()
                im.crop(rect).convert("RGB").save(out, format="JPEG", quality=95)
                output.append(
                    NativeCrop(
                        PageImage(
                            page.page_index, out.getvalue(), rect[2] - rect[0], rect[3] - rect[1]
                        ),
                        rect,
                        im.size,
                        row,
                    )
                )
    return tuple(output)
