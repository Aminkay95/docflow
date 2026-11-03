"""Independent semantic row inventory and bounded native-resolution crops."""

from __future__ import annotations

import io
import math
import re
from collections import defaultdict

from trade_extract.config import Settings
from trade_extract.domain.inference import PageImage
from trade_extract.domain.readers import (
    InventoryRow,
    NativeCrop,
    PageInput,
    TableInventory,
    TableRegion,
)
from trade_extract.schemas.base import TableMeta


def build_inventory(
    pages: tuple[PageInput, ...], required_pages: set[int], settings: Settings
) -> TableInventory:
    rows: list[InventoryRow] = []
    errors: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    for page in sorted(pages, key=lambda p: p.page_index):
        e = page.ocr
        if e is None or e.structure_status != "ok" or e.status != "ok":
            if page.page_index in required_pages:
                errors.append(f"page:{page.page_index}:independent_structure_unavailable")
            continue
        if not e.tables and page.page_index in required_pages:
            errors.append(f"page:{page.page_index}:independent_table_not_detected")
        previous: list[TableRegion] = []
        for table in sorted(e.tables, key=lambda t: (t.bbox.y0, t.bbox.x0, t.region_id)):
            if any(
                min(t.bbox.x1, table.bbox.x1) > max(t.bbox.x0, table.bbox.x0)
                and min(t.bbox.y1, table.bbox.y1) > max(t.bbox.y0, table.bbox.y0)
                for t in previous
            ):
                errors.append(f"page:{page.page_index}:overlapping_table_regions")
            previous.append(table)
            errors.extend(f"page:{page.page_index}:{d}" for d in table.diagnostics)
            if table.table_name is None:
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
                rows.append(
                    InventoryRow(
                        table.table_name,
                        counts[table.table_name],
                        page.page_index,
                        table.region_id,
                        row.bbox,
                    )
                )
                counts[table.table_name] += 1
    return TableInventory(tuple(rows), tuple(errors))


def reconcile(
    inventory: TableInventory, fields: dict, tables: dict[str, TableMeta]
) -> tuple[str, ...]:
    errors = list(inventory.diagnostics)
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
