"""Conservative local extraction from already trusted page evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from trade_extract.domain.fields import Agreement, Field, ValueType
from trade_extract.domain.provenance import BBox, CoordinateSpace, Provenance, ReaderKind
from trade_extract.domain.readers import FieldCandidate, PageInput, TextCharacter
from trade_extract.schemas.registry import SchemaSpec

VERSION = "local-labels-v1"


@dataclass(frozen=True)
class EvidenceLine:
    evidence_id: str
    page_index: int
    text: str
    bbox: BBox
    reader: ReaderKind
    characters: tuple[TextCharacter, ...] = ()

    def box_for(self, start: int, end: int) -> BBox | None:
        if not self.characters or start < 0 or end > len(self.characters) or start >= end:
            return None
        chars = self.characters[start:end]
        return BBox(
            x0=min(char.bbox.x0 for char in chars),
            y0=min(char.bbox.y0 for char in chars),
            x1=max(char.bbox.x1 for char in chars),
            y1=max(char.bbox.y1 for char in chars),
        )


_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "commercial_invoice": (
        (
            "invoice.number",
            r"(?:commercial\s+)?invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})",
        ),
        (
            "invoice.issue_date",
            r"(?:invoice\s+date|issue\s+date|date\s+of\s+issue)\s*[:\-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})",
        ),
        ("invoice.currency", r"(?:invoice\s+currency|currency)\s*[:\-]?\s*([A-Z]{3})\b"),
        ("seller.name", r"(?:seller|supplier|vendor)\s*(?:name)?\s*[:\-]\s*(.{2,80})"),
        ("buyer.name", r"(?:buyer|bill\s+to|customer)\s*(?:name)?\s*[:\-]\s*(.{2,80})"),
        (
            "totals.line_extension_amount",
            r"(?:line\s+extension|subtotal|sub\s+total)\s*(?:amount)?\s*[:\-]?\s*"
            r"(?:[A-Z]{3}\s*)?([0-9][0-9, .]*\d)",
        ),
        (
            "totals.tax_exclusive_amount",
            r"(?:tax\s+exclusive|total\s+before\s+tax)\s*(?:amount)?\s*[:\-]?\s*"
            r"(?:[A-Z]{3}\s*)?([0-9][0-9, .]*\d)",
        ),
        (
            "totals.tax_inclusive_amount",
            r"(?:tax\s+inclusive|total\s+including\s+tax|grand\s+total)\s*"
            r"(?:amount)?\s*[:\-]?\s*(?:[A-Z]{3}\s*)?([0-9][0-9, .]*\d)",
        ),
        (
            "totals.payable_amount",
            r"(?:amount\s+due|amount\s+payable|total\s+payable)\s*[:\-]?\s*"
            r"(?:[A-Z]{3}\s*)?([0-9][0-9, .]*\d)",
        ),
    ),
    "delivery_order": (
        (
            "release.place_of_delivery",
            r"\bplace\s+of\s+delivery\s*:\s*(.+)$",
        ),
        (
            "bill_of_lading.number",
            r"^(?:m\s*/?\s*b\s*/?\s*l|master\s+bill\s+of\s+lading|b\s*/?\s*l|"
            r"bill\s+of\s+lading)\s*(?:no\.?|number|#)\s*[:\-]?\s*"
            r"([A-Z0-9][A-Z0-9./_-]{2,})",
        ),
        (
            "delivery_order.issue_date",
            r"^(?:to\b.*?\s+)?(?:delivery\s+order\s+date|d/o\s+date|issue\s+date|date)"
            r"\s*:\s*(\d{1,2}[- ][A-Z]{3,9}[- ]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
            r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\s*$",
        ),
        (
            "consignee.name",
            r"^consignee(?:\s+name)?\s*:\s*(.+?)(?=\s+place\s+of\s+delivery\s*:|$)",
        ),
    ),
    "proforma_invoice": (
        (
            "invoice.number",
            r"proforma(?:\s+invoice)?\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})",
        ),
        (
            "invoice.issue_date",
            r"(?:invoice\s+date|issue\s+date|date)\s*[:\-]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})",
        ),
        ("invoice.currency", r"(?:invoice\s+currency|currency)\s*[:\-]?\s*([A-Z]{3})\b"),
    ),
}


def _ink_center(row: list[TextCharacter]) -> float:
    ink = [char for char in row if not char.text.isspace() and char.text not in ",.;:"]
    if not ink:
        ink = [char for char in row if not char.text.isspace()]
    return sum((char.bbox.y0 + char.bbox.y1) / 2 for char in ink) / len(ink)


def evidence_lines(
    pages: tuple[PageInput, ...], *, preserve_text_order: bool = False
) -> tuple[EvidenceLine, ...]:
    """Build compact, stable line evidence without importing image/OCR libraries."""
    output: list[EvidenceLine] = []
    for page in pages:
        page_line_count = 0
        if page.is_unreadable:
            continue
        if page.characters:
            groups: list[list[TextCharacter]] = []
            for char in page.characters:
                if char.text in {"\n", "\r"}:
                    if groups and groups[-1]:
                        groups.append([])
                    continue
                if preserve_text_order:
                    if not groups:
                        groups.append([])
                    groups[-1].append(char)
                    continue
                # Spaces may have baseline-only PDF rectangles. They belong to
                # their source line but must not shift its ink baseline.
                if char.text.isspace():
                    if groups and groups[-1]:
                        groups[-1].append(char)
                    continue
                if char.text in ",.;:" and groups and groups[-1]:
                    previous = next((c for c in reversed(groups[-1]) if c.text.strip()), None)
                    if (
                        previous is not None
                        and -0.002 <= char.bbox.x0 - previous.bbox.x1 <= 0.025
                        and abs(char.bbox.y1 - previous.bbox.y1) <= max(0.004, char.bbox.height)
                    ):
                        groups[-1].append(char)
                        continue
                center = (char.bbox.y0 + char.bbox.y1) / 2
                group = next(
                    (
                        row
                        for row in reversed(groups[-3:])
                        if row
                        and abs(center - _ink_center(row)) <= max(0.003, char.bbox.height * 0.6)
                    ),
                    None,
                )
                if group is None:
                    group = []
                    groups.append(group)
                group.append(char)
            for row in groups:
                if not row:
                    continue
                if not preserve_text_order:
                    row.sort(key=lambda char: (char.bbox.x0, char.bbox.y0))
                text = "".join(char.text for char in row).strip()
                if not text:
                    continue
                output.append(
                    EvidenceLine(
                        evidence_id=f"p{page.page_index}l{page_line_count}",
                        page_index=page.page_index,
                        text=text,
                        bbox=_box(char.bbox for char in row),
                        reader=ReaderKind.TEXT_LAYER,
                        characters=tuple(row),
                    )
                )
                page_line_count += 1
        elif page.ocr:
            for span in page.ocr.spans:
                if span.text.strip():
                    output.append(
                        EvidenceLine(
                            evidence_id=f"p{page.page_index}l{page_line_count}",
                            page_index=page.page_index,
                            text=span.text,
                            bbox=span.bbox,
                            reader=ReaderKind.PADDLE,
                        )
                    )
                    page_line_count += 1
    return tuple(output)


_DELIVERY_REFERENCE_LABEL = re.compile(
    r"(?P<delivery>\b(?:delivery\s+order|d\s*[./]?\s*o\.?)\s*"
    r"(?:no\.?|number\b|#))"
    r"|\b(?:(?:house|master)\s+)?(?:bill\s+of\s+lading|[hm]?b\s*/?\s*l)\s*"
    r"(?:no\.?|number\b|#)",
    re.IGNORECASE,
)


def delivery_number_matches_label(field: Field, lines: tuple[EvidenceLine, ...]) -> bool:
    """Associate a proposed D/O number with a label in its visual column."""
    provenance = field.provenance
    value_box = provenance.bbox
    nearby: list[tuple[float, bool]] = []
    saw_label = False
    for line in lines:
        if line.page_index != provenance.page_index:
            continue
        text = "".join(char.text for char in line.characters) if line.characters else line.text
        labels = list(_DELIVERY_REFERENCE_LABEL.finditer(text))
        for index, label in enumerate(labels):
            is_delivery = label.group("delivery") is not None
            saw_label = True
            box = line.box_for(*label.span()) or line.bbox
            if box == value_box:
                end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
                tail = text[label.end() : end]
                if re.search(r"(?<!\w)" + re.escape(field.value) + r"(?!\w)", tail, re.I):
                    nearby.append((0.0, is_delivery))
                continue
            same_row = abs((box.y0 + box.y1) - (value_box.y0 + value_box.y1)) <= (
                max(box.height, value_box.height)
            )
            if same_row and 0 <= value_box.x0 - box.x1 <= 0.25:
                nearby.append((value_box.x0 - box.x1, is_delivery))
                continue
            below_label = 0 <= value_box.y0 - box.y1 <= 0.025
            same_column = box.x0 - 0.01 <= value_box.x0 <= box.x1 + 0.02
            if below_label and same_column:
                nearby.append((0.26 + value_box.y0 - box.y1, is_delivery))
    if not nearby:
        return not saw_label
    distance = min(distance for distance, _ in nearby)
    return all(is_delivery for gap, is_delivery in nearby if gap == distance)


_DELIVERY_VOLUME = re.compile(r"\bvolume\b", re.IGNORECASE)
_DELIVERY_GROSS_WEIGHT = re.compile(
    r"\b(?:g\s*\.\s*weight|gross\s+weight)\b(?:\s*\(?\s*(?P<kg>kg)\b\)?)?",
    re.IGNORECASE,
)
_DELIVERY_NET_WEIGHT = re.compile(
    r"\b(?:n\s*\.\s*weight|net\s+weight)\b(?:\s*\(?\s*(?P<kg>kg)\b\)?)?",
    re.IGNORECASE,
)
_PRINTED_NUMBER = re.compile(r"(?<![\w.,])\d[\d,.]*\d(?![\w.,])|(?<![\w.,])\d(?![\w.,])")


def reconcile_delivery_measurements(
    fields: dict[str, Field], lines: tuple[EvidenceLine, ...]
) -> None:
    """When both headings are visible, ground weights and volumes in their own columns."""
    headings: dict[int, list[dict[str, BBox]]] = {}
    for line in lines:
        volume = _DELIVERY_VOLUME.search(line.text)
        gross = _DELIVERY_GROSS_WEIGHT.search(line.text)
        if volume and gross:
            volume_box = line.box_for(*volume.span())
            gross_box = line.box_for(*gross.span())
            if volume_box and gross_box:
                columns = {
                    "volume": volume_box,
                    "gross_weight_kg" if gross.group("kg") else "gross_weight": gross_box,
                }
                net = _DELIVERY_NET_WEIGHT.search(line.text)
                if net and (net_box := line.box_for(*net.span())):
                    columns["net_weight_kg" if net.group("kg") else "net_weight"] = net_box
                headings.setdefault(line.page_index, []).append(columns)
    for path, container in tuple(fields.items()):
        if not re.fullmatch(r"containers\[\d+\]\.number", path):
            continue
        provenance = container.provenance
        if provenance.reader != ReaderKind.TEXT_LAYER:
            continue
        row_box = provenance.bbox
        headers = [
            header
            for header in headings.get(provenance.page_index, ())
            if max(box.y1 for box in header.values()) < row_box.y0
        ]
        if not headers:
            continue
        columns = max(headers, key=lambda header: header["volume"].y0)
        prefix = path.removesuffix(".number")
        for key in columns:
            fields.pop(f"{prefix}.{key.removesuffix('_kg')}", None)
            fields.pop(f"{prefix}.{key.removesuffix('_kg')}_kg", None)
        centers = {key: (box.x0 + box.x1) / 2 for key, box in columns.items()}
        candidates: dict[str, list[tuple[str, BBox]]] = {key: [] for key in columns}
        for line in lines:
            if line.page_index != provenance.page_index or not line.characters:
                continue
            if line.bbox.y0 > row_box.y1 or line.bbox.y1 < row_box.y0:
                continue
            text = "".join(char.text for char in line.characters)
            for match in _PRINTED_NUMBER.finditer(text):
                box = line.box_for(*match.span())
                if box is None or box.y0 > row_box.y1 or box.y1 < row_box.y0:
                    continue
                center = (box.x0 + box.x1) / 2
                nearest = sorted((abs(center - x), key) for key, x in centers.items())
                if nearest[0][0] < min(0.035, nearest[1][0]):
                    candidates[nearest[0][1]].append((match.group(), box))
        for key, matches in candidates.items():
            if len(matches) == 1:
                value, box = matches[0]
                fields[f"{prefix}.{key}"] = Field(
                    kind="scalar",
                    value=value,
                    raw_text=value,
                    value_type=ValueType.DECIMAL,
                    agreement=Agreement.SINGLE_READER,
                    provenance=provenance.model_copy(
                        update={"bbox": box, "reader_version": VERSION}
                    ),
                )


def delivery_cargo_description(
    fields: dict[str, Field], lines: tuple[EvidenceLine, ...]
) -> Field | None:
    """Read a single container's wrapped STC description up to a freight clause."""
    containers = [
        field for path, field in fields.items() if re.fullmatch(r"containers\[\d+\]\.number", path)
    ]
    if len(containers) != 1 or containers[0].provenance.reader != ReaderKind.TEXT_LAYER:
        return None
    provenance = containers[0].provenance
    row = provenance.bbox
    page_lines = [line for line in lines if line.page_index == provenance.page_index]
    for header in reversed(page_lines):
        if not header.characters or header.bbox.y1 >= row.y0:
            continue
        description = re.search(r"\bdescription\s+of\s+goods\b", header.text, re.I)
        volume = _DELIVERY_VOLUME.search(header.text)
        if description and volume:
            left = header.box_for(*description.span())
            right = header.box_for(*volume.span())
            if left and right and left.x1 < right.x0:
                break
    else:
        return None
    fragments: list[TextCharacter] = []
    previous_bottom = row.y0
    for line in sorted(page_lines, key=lambda item: item.bbox.y0):
        if line.bbox.y1 < row.y0 or not line.characters:
            continue
        if line.bbox.y0 - previous_bottom > 0.015:
            break
        chars = [
            char
            for char in line.characters
            if left.x0 - 0.01 <= char.bbox.x0 and char.bbox.x1 < right.x0 - 0.005
        ]
        if not chars or not any(char.text.strip() for char in chars):
            continue
        if fragments:
            fragments.append(TextCharacter(" ", chars[0].bbox))
        fragments.extend(chars)
        previous_bottom = max(previous_bottom, line.bbox.y1)
    text = "".join(char.text for char in fragments)
    match = re.search(r"^\s*STC\s*:\s*(.+?)\s+FREIGHT\s+(?:PREPAID|COLLECT)\b", text, re.I)
    if not match:
        return None
    value = " ".join(match.group(1).split())
    box = _box(char.bbox for char in fragments[match.start(1) : match.end(1)])
    return Field(
        kind="scalar",
        value=value,
        raw_text=value,
        value_type=ValueType.STRING,
        agreement=Agreement.SINGLE_READER,
        provenance=provenance.model_copy(update={"bbox": box, "reader_version": VERSION}),
    )


def compact_evidence(lines: tuple[EvidenceLine, ...], *, max_characters: int) -> str:
    rendered: list[str] = []
    used = 0
    for line in lines:
        entry = (
            f"{line.evidence_id}|p={line.page_index}|"
            f"b={line.bbox.x0:.5f},{line.bbox.y0:.5f},{line.bbox.x1:.5f},{line.bbox.y1:.5f}|"
            f"{line.text}"
        )
        if used + len(entry) + 1 > max_characters:
            break
        rendered.append(entry)
        used += len(entry) + 1
    return "\n".join(rendered)


def extract_local(
    document_type: str,
    spec: SchemaSpec,
    source_hash: str,
    lines: tuple[EvidenceLine, ...],
) -> tuple[FieldCandidate, ...]:
    """Return only unique, exact same-line label matches with source geometry."""
    candidates: list[FieldCandidate] = []
    for path, pattern in _RULES.get(document_type, ()):
        field_spec = spec.model.field_set.resolve(path)
        if field_spec is None:
            continue
        matches: list[tuple[EvidenceLine, re.Match[str]]] = []
        compiled = re.compile(pattern, re.IGNORECASE)
        for line in lines:
            match = compiled.search(line.text)
            if match:
                matches.append((line, match))
        if not matches and path == "totals.payable_amount":
            # Some PDFs store the labelled balance and its amount in separate
            # text runs. Associate them by their printed position.
            for anchor in lines:
                if not re.fullmatch(
                    r"(?:balance(?: due)?|amount due|amount payable|total payable)\s*:",
                    anchor.text,
                    re.I,
                ):
                    continue
                for line in lines:
                    if (
                        line.page_index == anchor.page_index
                        and 0 <= line.bbox.x0 - anchor.bbox.x1 <= 0.3
                        and abs((line.bbox.y0 + line.bbox.y1 - anchor.bbox.y0 - anchor.bbox.y1) / 2)
                        <= 0.006
                        and (
                            match := re.fullmatch(
                                r"((?:[A-Z]{3}\s*|[$\u20ac\u00a3]\s*)?[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)",
                                line.text,
                            )
                        )
                    ):
                        matches.append((line, match))
        values = {match.group(1).strip() for _, match in matches}
        if len(matches) != 1 or len(values) != 1:
            continue
        line, match = matches[0]
        value = match.group(1).strip().rstrip(".,;:")
        start = match.start(1) + len(match.group(1)) - len(match.group(1).lstrip())
        end = start + len(value)
        bbox = line.box_for(start, end)
        # OCR spans do not provide character-level geometry. Only accept a whole-span
        # value there; labelled OCR lines remain provider evidence, not local truth.
        if bbox is None:
            continue
        candidates.append(
            FieldCandidate(
                path,
                Field(
                    kind="scalar",
                    value=value,
                    value_type=field_spec.value_type,
                    raw_text=value,
                    agreement=Agreement.SINGLE_READER,
                    provenance=Provenance(
                        page_index=line.page_index,
                        page_number=line.page_index + 1,
                        bbox=bbox,
                        coordinate_space=CoordinateSpace.NORMALIZED_TOP_LEFT,
                        reader=line.reader,
                        reader_version=VERSION,
                        model=VERSION,
                        document_hash=source_hash,
                    ),
                ),
            )
        )
    return tuple(candidates)


def _box(boxes: Iterable[BBox]) -> BBox:
    values: tuple[BBox, ...] = tuple(boxes)
    return BBox(
        x0=min(box.x0 for box in values),
        y0=min(box.y0 for box in values),
        x1=max(box.x1 for box in values),
        y1=max(box.y1 for box in values),
    )
