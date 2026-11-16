"""Read numbered container manifests printed over a raster bill-of-lading form."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from trade_extract.domain.fields import Agreement, Field
from trade_extract.domain.provenance import BBox, CoordinateSpace, Provenance, ReaderKind
from trade_extract.domain.readers import FieldCandidate, InventoryRow, PageInput, TableInventory
from trade_extract.schemas.bill_of_lading import BL_FIELD_SET
from trade_extract.services.local_extraction import EvidenceLine, evidence_lines

_VERSION = "digital-container-manifest-v1"
_ROW = re.compile(
    r"\s*(?P<marks>\d{1,3})\s+(?P<number>[A-Z]{4}\d{7})\s+"
    r"(?P<seal>[A-Z0-9-]{4,20})\s+(?P<package_count>\d+)\s+"
    r"(?P<package_type>Drum\(s\))(?=\s|$)",
    re.IGNORECASE,
)
_CONTAINER = re.compile(r"\b[A-Z]{4}\d{7}\b", re.IGNORECASE)
_STC = re.compile(r"\bSTC\s*:\s*$", re.IGNORECASE)
_MANIFEST_HEADING = re.compile(r"(\d+)\s*x\s*\d+'FCL/FCL\s+CONTAINERS", re.IGNORECASE)
_HS = re.compile(r"HS\s+CODE:\s*(\d{6,10})\s*$", re.IGNORECASE)
_WEIGHT = re.compile(r"(?<!\S)(\d[\d,]*\.\d{2})\s*$")
_GROSS_TOTAL = re.compile(r"\s*TOTAL GROSS WEIGHT:\s*(\d[\d,]*)\s+MT\s*$", re.I)


@dataclass(frozen=True)
class DigitalManifest:
    inventory: TableInventory
    candidates: tuple[FieldCandidate, ...]


def read_manifest(page: PageInput, document_hash: str, row_limit: int) -> DigitalManifest | None:
    """Recognize a narrow form layout with a numbered manifest and shared STC block."""
    lines = sorted(evidence_lines((page,), preserve_text_order=True), key=lambda line: line.bbox.y0)
    rows = []
    for line in lines:
        text = "".join(char.text for char in line.characters)
        if not _CONTAINER.search(text):
            continue
        match = _ROW.match(text)
        if match is None:
            return None
        rows.append((line, match, text))

    if not 4 <= len(rows) < row_limit:
        return None
    if [int(match["marks"]) for _, match, _ in rows] != list(range(1, len(rows) + 1)):
        return None
    if len({match["number"].upper() for _, match, _ in rows}) != len(rows):
        return None

    for key in ("number", "seal", "package_count", "package_type"):
        anchors = [line.characters[match.start(key)].bbox.x0 for line, match, _ in rows]
        if max(anchors) - min(anchors) > 0.012:
            return None
    if any(
        not 0 < right.bbox.y0 - left.bbox.y0 <= 0.025
        for (left, _, _), (right, _, _) in zip(rows, rows[1:], strict=False)
    ):
        return None

    # The last printed number is a separate column. The shared description starts
    # after the package type and may occupy the same baseline as several containers.
    notes = []
    for line, match, text in rows:
        weight = _WEIGHT.search(text, match.end())
        tail = text[match.end() : weight.start() if weight else len(text)]
        if tail.strip():
            notes.append((line, tail.strip(), match.end() + len(tail) - len(tail.lstrip())))
    if (
        len(notes) < 3
        or (heading := _MANIFEST_HEADING.fullmatch(notes[0][1])) is None
        or int(heading[1]) != len(rows)
        or not _STC.search(notes[1][1])
        or not re.fullmatch(r"[A-Z][A-Z0-9 /.-]{4,70}", notes[2][1])
    ):
        return None

    inventory = []
    candidates = []

    def add(path: str, line: EvidenceLine, start: int, value: str) -> BBox:
        box = line.box_for(start, start + len(value))
        spec = BL_FIELD_SET.resolve(path)
        assert box is not None and spec is not None
        candidates.append(
            FieldCandidate(
                path,
                Field(
                    kind="scalar",
                    value=value,
                    value_type=spec.value_type,
                    raw_text=value,
                    agreement=Agreement.SINGLE_READER,
                    provenance=Provenance(
                        page_index=page.page_index,
                        page_number=page.page_index + 1,
                        bbox=box,
                        coordinate_space=CoordinateSpace.NORMALIZED_TOP_LEFT,
                        reader=ReaderKind.TEXT_LAYER,
                        reader_version=_VERSION,
                        model=_VERSION,
                        document_hash=document_hash,
                    ),
                ),
            )
        )
        return box

    weights = []
    for line in lines:
        if match := _WEIGHT.search("".join(char.text for char in line.characters)):
            box = line.box_for(*match.span(1))
            assert box is not None
            if box.x0 > 0.7:
                weights.append((line, match, box))
    totals = [
        Decimal(match[1].replace(",", ""))
        for line in lines
        if (match := _GROSS_TOTAL.fullmatch(line.text))
    ]
    # This form prints a right-aligned gross-weight column, including a final
    # column total. Use the printed total only as a check on the row mapping.
    aligned_weights = (
        len(weights) == len(rows) + 1
        and bool(totals)
        and sum(Decimal(match[1].replace(",", "")) for _, match, _ in weights[:-1])
        == Decimal(weights[-1][1][1].replace(",", ""))
        == totals[0]
        and all(
            abs(line.bbox.y0 - box.y0) < 0.006
            for (line, _, _), (_, _, box) in zip(rows, weights[:-1], strict=True)
        )
        and all(abs(box.x1 - weights[-1][2].x1) < 0.012 for _, _, box in weights)
    )
    for index, (line, match, _) in enumerate(rows):
        columns = [
            (key, add(f"containers[{index}].{key}", line, match.start(key), match[key]))
            for key in ("marks", "number", "seal", "package_count", "package_type")
        ]
        row_box = line.bbox
        if aligned_weights:
            weight_line, weight, weight_box = weights[index]
            columns.append(
                (
                    "gross_weight_kg",
                    add(
                        f"containers[{index}].gross_weight_kg",
                        weight_line,
                        weight.start(1),
                        weight[1],
                    ),
                )
            )
            row_box = BBox(
                x0=row_box.x0,
                y0=min(row_box.y0, weight_box.y0),
                x1=max(row_box.x1, weight_box.x1),
                y1=max(row_box.y1, weight_box.y1),
            )
        inventory.append(
            InventoryRow(
                "containers", index, page.page_index, "digital-containers", row_box, tuple(columns)
            )
        )

    line, description, start = notes[2]
    description_box = add("line_items[0].description", line, start, description)
    cargo_columns = [("description", description_box)]
    for note, text, offset in notes[3:]:
        if hs := _HS.fullmatch(text):
            cargo_columns.append(
                ("hs_code", add("line_items[0].hs_code", note, offset + hs.start(1), hs[1]))
            )
            break
    cargo_box = description_box.model_copy(
        update={
            "x0": min(box.x0 for _, box in cargo_columns),
            "y0": min(box.y0 for _, box in cargo_columns),
            "x1": max(box.x1 for _, box in cargo_columns),
            "y1": max(box.y1 for _, box in cargo_columns),
        }
    )
    inventory.append(
        InventoryRow(
            "line_items",
            0,
            page.page_index,
            "digital-shared-cargo",
            cargo_box,
            tuple(cargo_columns),
        )
    )
    return DigitalManifest(TableInventory(tuple(inventory)), tuple(candidates))
