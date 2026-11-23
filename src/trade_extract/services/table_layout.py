"""Match printed table headers to schema columns using source geometry."""

from __future__ import annotations

import re
from dataclasses import dataclass

from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput, TableRegion, TableRow
from trade_extract.schemas.registry import SchemaSpec
from trade_extract.services.local_extraction import EvidenceLine, evidence_lines

_ALIASES = {
    "number": ("container", "container no", "container number", "container id"),
    "seal": ("seal", "seal no", "seal number"),
    "type": ("size", "container size", "size type", "container type"),
    "description": ("description", "description of goods", "goods description", "item description"),
    "line_number": ("line", "line no", "sr no"),
    "item_number": ("item", "item no", "item number"),
    "hs_code": ("hs code", "commodity code", "tariff code"),
    "marks": ("marks", "marks and numbers"),
    "package_count": ("packages", "no of pkgs", "no of packages", "package count"),
    "package_type": ("package type", "kind of packages"),
    "gross_weight": ("g weight", "gross weight"),
    "gross_weight_kg": ("g weight kg", "gross weight kg", "gross wt kg"),
    "net_weight": ("n weight", "net weight"),
    "net_weight_kg": ("n weight kg", "net weight kg", "net wt kg"),
    "volume": ("volume", "cbm"),
    "volume_m3": ("volume m3", "volume m³", "measurement m3", "meas m³"),
    "quantity": ("qty", "quantity"),
    "unit_code": ("unit", "uom", "unit of measure"),
    "unit_of_measure": ("unit", "uom", "unit of measure"),
    "unit_price": ("unit price", "price per unit", "unit rate"),
    "line_extension_amount": ("amount", "line amount", "line total", "extended price"),
    "text": ("finding", "findings", "observation", "observations"),
}
_UNMAPPED_COLUMNS = ("v weight", "volumetric weight")
_CONTAINER_ID = re.compile(r"\b[A-Z]{4}\s*\d{7}\b", re.I)
_NUMBER = re.compile(r"^[+-]?\d[\d,. ]*(?:\s*[A-Z]{1,4})?$", re.I)


def _normal(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _aliases(spec: SchemaSpec, table: str) -> dict[str, str]:
    descriptor = next(item for item in spec.model.table_specs if item.name == table)
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for column in spec.model.field_set.columns_for(descriptor):
        for alias in (column.label, column.key, *_ALIASES.get(column.key, ())):
            name = _normal(alias)
            if name in aliases and aliases[name] != column.key:
                ambiguous.add(name)
            aliases[name] = column.key
    return {name: key for name, key in aliases.items() if name not in ambiguous}


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer.x0 - 1e-5 <= inner.x0 <= inner.x1 <= outer.x1 + 1e-5
        and outer.y0 - 1e-5 <= inner.y0 <= inner.y1 <= outer.y1 + 1e-5
    )


def _cell_text(page: PageInput, cell: BBox) -> str:
    if page.characters:
        return "".join(char.text for char in page.characters if _contains(cell, char.bbox))
    return (
        " ".join(span.text for span in page.ocr.spans if _contains(cell, span.bbox))
        if page.ocr
        else ""
    )


def ocr_table_columns(
    page: PageInput, region: TableRegion, spec: SchemaSpec
) -> tuple[str | None, tuple[str | None, ...]]:
    """Identify an OCR grid only when its printed headers pick a unique table."""
    header = next((row for row in region.rows if row.header and row.cells), None)
    if header is None:
        return region.table_name, ()
    headings = [
        _normal(_cell_text(page, cell)) for cell in sorted(header.cells, key=lambda b: b.x0)
    ]
    matches = []
    for name in sorted(spec.tables):
        if region.table_name and name != region.table_name:
            continue
        aliases = _aliases(spec, name)
        keys = tuple(aliases.get(heading) for heading in headings)
        mapped = [key for key in keys if key is not None]
        if len(mapped) >= min(2, len(set(aliases.values()))) and len(mapped) == len(set(mapped)):
            matches.append((len(mapped), name, keys))
    if not matches:
        return region.table_name, ()
    matches.sort(reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return region.table_name, ()
    return matches[0][1], matches[0][2]


@dataclass(frozen=True)
class DigitalGrid:
    name: str
    rows: tuple[TableRow, ...]
    columns: tuple[str | None, ...]


def _header(line: EvidenceLine, aliases: dict[str, str]) -> list[tuple[str | None, BBox]]:
    text = "".join(char.text for char in line.characters)
    accepted: list[tuple[int, int, str | None, BBox]] = []
    for label in sorted((*aliases, *_UNMAPPED_COLUMNS), key=lambda alias: (-len(alias), alias)):
        pattern = r"(?<!\w)" + r"[\s.&/-]+".join(map(re.escape, label.split())) + r"(?!\w)"
        for match in re.finditer(pattern, text, re.I):
            start, end = match.span()
            if any(start < right and end > left for left, right, _, _ in accepted):
                continue
            glyphs = [char for char in line.characters[start:end] if char.text.strip()]
            if any(
                right.bbox.x0 - left.bbox.x1 > 0.025
                for left, right in zip(glyphs, glyphs[1:], strict=False)
            ):
                continue
            box = line.box_for(start, end)
            if box:
                accepted.append((start, end, aliases.get(label), box))
    return sorted([(key, box) for _, _, key, box in accepted], key=lambda part: part[1].x0)


def _row_anchor(name: str, text: str) -> bool:
    if name == "containers":
        return bool(_CONTAINER_ID.search(text))
    if name in {"line_items", "goods_lines", "assessment"}:
        return bool(text) and not re.match(
            r"^(?:sub\s*total|grand\s+total|total|balance)\b", text, re.I
        )
    return False


def digital_grids(page: PageInput, spec: SchemaSpec, row_limit: int) -> tuple[DigitalGrid, ...]:
    """Use positioned digital text when distinct printed headers identify a grid."""
    if not page.characters or not spec.tables:
        return ()
    lines = evidence_lines((page,), preserve_text_order=True)
    grids = []
    positioned = sorted(lines, key=lambda item: item.bbox.y0)
    alias_maps = {name: _aliases(spec, name) for name in sorted(spec.tables)}
    for line in lines:
        # Wide source runs can contain several headings; ordinary labels and prose cannot.
        if line.bbox.width < 0.2 or len(line.text) > 240:
            continue
        matches = []
        for name, aliases in alias_maps.items():
            if name == "line_items" and _normal(line.text) == "item quantity rate amount":
                # This four-column header uses Item for the goods description.
                aliases = {**aliases, "item": "description", "rate": "unit_price"}
            columns = _header(line, aliases)
            keys = [key for key, _ in columns if key is not None]
            if len(keys) < 2 or len(set(keys)) != len(keys):
                continue
            if name == "containers" and "number" not in keys:
                continue
            if name in {"line_items", "goods_lines"} and "description" not in keys:
                continue
            matches.append((len(keys), name, columns))
        matches.sort(key=lambda item: item[0], reverse=True)
        if not matches or (len(matches) > 1 and matches[0][0] == matches[1][0]):
            continue
        _, name, columns = matches[0]
        centers = [(box.x0 + box.x1) / 2 for _, box in columns]
        if any(right - left < 0.015 for left, right in zip(centers, centers[1:], strict=False)):
            continue
        edges = [max(0.0, columns[0][1].x0 - 0.025)]
        for index, (left, right) in enumerate(zip(centers, centers[1:], strict=False)):
            key, _ = columns[index]
            next_key, next_box = columns[index + 1]
            boundary = (left + right) / 2
            if next_key == "description":
                boundary = next_box.x0 - 0.004
            elif next_key == "package_count":
                # A right-aligned count can begin left of its header.
                boundary = min(boundary, next_box.x0 - 0.04)
            elif key in {"number", "description", "type", "seal"}:
                boundary = max(boundary, next_box.x0 - 0.018)
            edges.append(boundary)
        edges.append(min(1.0, columns[-1][1].x1 + 0.05))
        if any(right <= left for left, right in zip(edges, edges[1:], strict=False)):
            continue
        rows: list[TableRow] = []
        last_bottom = line.bbox.y1
        for source in positioned:
            if source.bbox.y0 <= line.bbox.y1:
                continue
            if source.bbox.y0 - last_bottom > 0.045:
                break
            if rows and source.bbox.y0 < last_bottom:
                continue
            if not _row_anchor(name, source.text):
                continue
            bottom = source.bbox.y1
            if "description" in [key for key, _ in columns]:
                description_index = next(
                    i for i, (key, _) in enumerate(columns) if key == "description"
                )
                for continuation in positioned:
                    if continuation.bbox.y0 <= source.bbox.y1:
                        continue
                    if continuation.bbox.y0 - bottom > 0.016:
                        break
                    if not (
                        edges[description_index] <= continuation.bbox.x0
                        and continuation.bbox.x1 <= edges[description_index + 1]
                    ):
                        break
                    bottom = continuation.bbox.y1
            cells = tuple(
                BBox(x0=left, x1=right, y0=source.bbox.y0, y1=bottom)
                for left, right in zip(edges, edges[1:], strict=False)
            )
            texts = [
                "".join(
                    char.text for char in source.characters if _contains(cell, char.bbox)
                ).strip()
                for cell in cells
            ]
            if name != "containers" and (
                sum(bool(text) for text in texts) < 2
                or not any(_NUMBER.fullmatch(text) for text in texts)
            ):
                if rows:
                    break
                continue
            rows.append(
                TableRow(
                    BBox(x0=edges[0], x1=edges[-1], y0=source.bbox.y0, y1=bottom),
                    cells,
                )
            )
            last_bottom = bottom
            if len(rows) >= row_limit:
                break
        if rows:
            grids.append(DigitalGrid(name, tuple(rows), tuple(key for key, _ in columns)))
    # Repeated or competing layouts need the independent OCR inventory.
    return tuple(grid for grid in grids if sum(g.name == grid.name for g in grids) == 1)
