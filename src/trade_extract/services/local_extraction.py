"""Conservative local extraction from already trusted page evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from trade_extract.domain.fields import Agreement, Field
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


def evidence_lines(pages: tuple[PageInput, ...]) -> tuple[EvidenceLine, ...]:
    """Build compact, stable line evidence without importing image/OCR libraries."""
    output: list[EvidenceLine] = []
    for page in pages:
        if page.is_unreadable:
            continue
        if page.characters:
            groups: list[list[TextCharacter]] = []
            for char in page.characters:
                if char.text in {"\n", "\r"}:
                    if groups and groups[-1]:
                        groups.append([])
                    continue
                center = (char.bbox.y0 + char.bbox.y1) / 2
                group = next(
                    (
                        row
                        for row in reversed(groups[-3:])
                        if row
                        and abs(center - sum((c.bbox.y0 + c.bbox.y1) / 2 for c in row) / len(row))
                        <= max(0.003, char.bbox.height * 0.6)
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
                row.sort(key=lambda char: (char.bbox.x0, char.bbox.y0))
                text = "".join(char.text for char in row).strip()
                if not text:
                    continue
                output.append(
                    EvidenceLine(
                        evidence_id=f"p{page.page_index}l{len(output)}",
                        page_index=page.page_index,
                        text=text,
                        bbox=_box(char.bbox for char in row),
                        reader=ReaderKind.TEXT_LAYER,
                        characters=tuple(row),
                    )
                )
        elif page.ocr:
            for span in page.ocr.spans:
                if span.text.strip():
                    output.append(
                        EvidenceLine(
                            evidence_id=f"p{page.page_index}l{len(output)}",
                            page_index=page.page_index,
                            text=span.text.strip(),
                            bbox=span.bbox,
                            reader=ReaderKind.PADDLE,
                        )
                    )
    return tuple(output)


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
