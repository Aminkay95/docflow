from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Final

#: The eight recognised document types. ``unknown`` is an outcome, not a type.
RECOGNISED_TYPES: Final = (
    "commercial_invoice",
    "proforma_invoice",
    "bill_of_lading",
    "delivery_order",
    "port_pass",
    "custom_entry",
    "verification_memo",
    "packing_list",
)
UNKNOWN: Final = "unknown"
DEFAULT_MIN_CONFIDENCE: Final = 0.55
CONTINUATION_CONFIDENCE: Final = 0.6

#: Number of non-empty leading lines treated as the page title.
_TITLE_LINE_COUNT: Final = 4

#: A signal is ``(pattern, title_weight, body_weight)``. Each pattern contributes
#: at most once per page: its title weight when it appears in the header, its body
#: weight when it appears only lower down.
_Signal = tuple[re.Pattern[str], float, float]


def _signals(*rows: tuple[str, float, float]) -> tuple[_Signal, ...]:
    return tuple((re.compile(pattern, re.IGNORECASE), title, body) for pattern, title, body in rows)


#: Weighted lexicon. Strong titles dominate; body signals only break near-ties and
#: enrich a page whose header has been cropped away. Every pattern is deterministic
#: and independent of filename, declared content type, job reference and neighbours.
_LEXICON: Final[dict[str, tuple[_Signal, ...]]] = {
    "commercial_invoice": _signals(
        (r"\bcommercial\s+invoice\b", 6.0, 2.0),
        (r"\btax\s+invoice\b", 6.0, 2.0),
        (r"\binvoice\b", 2.5, 0.5),
        (r"\binvoice\s+(?:no|number|date)\b", 1.5, 0.75),
        (r"\bamount\s+(?:due|payable)\b", 0.0, 0.5),
        (r"\btotal\s+payable\b", 0.0, 0.5),
        (r"\bbill\s+to\b", 0.0, 0.5),
        (r"\bunit\s+price\b", 0.0, 0.4),
    ),
    "proforma_invoice": _signals(
        (r"\bproforma(?:\s+invoice)?\b", 6.5, 2.0),
        (r"\bvalid\s+until\b", 0.0, 0.5),
    ),
    "bill_of_lading": _signals(
        (r"\bbill\s+of\s+lading\b", 6.0, 2.0),
        (r"\bsea\s+waybill\b", 6.0, 2.0),
        (r"\bb/?l\b", 5.0, 1.5),
        (r"\bwaybill\b", 4.0, 1.5),
        (r"\bshipper\b", 0.0, 0.75),
        (r"\bconsignee\b", 0.0, 0.75),
        (r"\bnotify\s+party\b", 0.0, 0.75),
        (r"\bport\s+of\s+(?:loading|discharge)\b", 0.0, 0.75),
        (r"\bfreight\s+(?:prepaid|collect)\b", 0.0, 0.75),
        (r"\bvessel\b", 0.0, 0.5),
        (r"\bvoyage\b", 0.0, 0.5),
        (r"\bscac\b", 0.0, 0.5),
    ),
    "delivery_order": _signals(
        (r"\bdelivery\s+order\b", 6.0, 2.0),
        (r"\brelease\s+(?:order|reference)\b", 0.0, 0.5),
        (r"\bfree\s+time\b", 0.0, 0.5),
    ),
    "port_pass": _signals(
        (r"\bport\s+pass\b", 6.0, 2.0),
        (r"\bport\s+authority\s+pass\b", 6.0, 2.0),
        (r"\bgate\s+pass\b", 6.0, 2.0),
        (r"\bterminal\s+pass\b", 6.0, 2.0),
        (r"\bvehicle\b", 0.0, 0.5),
        (r"\bdriver\b", 0.0, 0.5),
        (r"\bplate\b", 0.0, 0.5),
        (r"\bescort\b", 0.0, 0.5),
    ),
    "custom_entry": _signals(
        (r"\bcustoms?\s+entry\b", 6.0, 2.0),
        (r"\bcustoms?\s+declaration\b", 6.0, 2.0),
        (r"\bimport\s+declaration\b", 6.0, 2.0),
        (r"\bentry\s+(?:no|number)\b", 1.5, 0.5),
        (r"\bdeclaration\s+(?:no|number)\b", 1.5, 0.5),
        (r"\bhs\s+code\b", 0.0, 0.5),
        (r"\bcustoms\s+value\b", 0.0, 0.5),
        (r"\bassessment\b", 0.0, 0.5),
        (r"\bduty\b", 0.0, 0.5),
    ),
    "verification_memo": _signals(
        (r"\bverification\s+memo\b", 6.0, 2.0),
        (r"\bverification\s+report\b", 6.0, 2.0),
        (r"\bcfs\s+verification\b", 6.0, 2.0),
        (r"\bcondition\s+report\b", 6.0, 2.0),
        (r"\bdamage\s+report\b", 6.0, 2.0),
        (r"\bshortage\s+report\b", 6.0, 2.0),
        (r"\bseal\s+intact\b", 0.0, 0.5),
        (r"\bpackages?\s+(?:received|damaged|short)\b", 0.0, 0.5),
        (r"\bfindings\b", 0.0, 0.5),
    ),
    "packing_list": _signals(
        (r"\bpacking\s+list\b", 6.0, 2.0),
        (r"\bpacking\s+slip\b", 6.0, 2.0),
        (r"\bcartons?\b", 0.0, 0.5),
        (r"\bpallets?\b", 0.0, 0.5),
        (r"\bnet\s+weight\b", 0.0, 0.5),
        (r"\bgross\s+weight\b", 0.0, 0.5),
        (r"\bmarks\s+and\s+numbers\b", 0.0, 0.5),
        (r"\bvolume\b", 0.0, 0.5),
    ),
}

_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class PageClassification:
    document_type: str
    confidence: float
    scores: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class SegmentSpec:
    page_start: int
    page_end: int
    document_type: str
    confidence: float
    fingerprint: str

    @property
    def page_count(self) -> int:
        return self.page_end - self.page_start + 1


def score_page(text: str) -> dict[str, float]:
    """Return the deterministic weighted score per recognised type."""

    title_lines = [line for line in text.splitlines() if line.strip()][:_TITLE_LINE_COUNT]
    title = "\n".join(title_lines)
    scores: dict[str, float] = {}
    for document_type, signals in _LEXICON.items():
        score = 0.0
        for pattern, title_weight, body_weight in signals:
            if title_weight > 0 and pattern.search(title):
                score += title_weight
            elif pattern.search(text):
                score += body_weight
        if score > 0:
            scores[document_type] = score
    return scores


def classify_page(
    text: str,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> PageClassification:
    """Classify one page from its embedded text only.

    Confidence is the top type's share of the top-two score, so two equally
    scoring types halve it. A tie, a share below ``min_confidence``, a raw top
    score below ``min_confidence``, or a page with no text at all is ``unknown``.
    The caller decides whether an ``unknown`` page is a continuation of the
    previous confident page.
    """

    scores = score_page(text)
    if not scores:
        return PageClassification(UNKNOWN, 0.0)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    total = top_score + second_score
    confidence = round(top_score / total, 4) if total > 0 else 0.0
    confidence = min(1.0, max(0.0, confidence))
    tied = len(ranked) > 1 and ranked[1][1] == top_score
    weak_evidence = top_score < min_confidence
    if tied or weak_evidence or confidence < min_confidence:
        # A page rejected only because its absolute evidence is weak has no
        # meaningful share (a single weak signal yields 1.0); report zero so the
        # result's ``document_type_confidence`` cannot read as confident.
        reported = confidence if tied or not weak_evidence else 0.0
        return PageClassification(UNKNOWN, reported, tuple(ranked))
    return PageClassification(top_type, confidence, tuple(ranked))


def segment_pages(
    page_texts: list[str] | tuple[str, ...],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    continuation_confidence: float = CONTINUATION_CONFIDENCE,
) -> tuple[SegmentSpec, ...]:
    """Split one file's pages into contiguous, non-overlapping typed segments.

    A page that classification cannot place inherits the last confident type at
    the fixed continuation confidence and merges into that segment. Pages before
    any confident page stay ``unknown``. Every page is covered exactly once.
    """

    texts = tuple(page_texts)
    if not texts:
        return ()

    classifications: list[PageClassification] = []
    previous_type: str | None = None
    for text in texts:
        result = classify_page(text, min_confidence=min_confidence)
        if result.document_type == UNKNOWN and previous_type is not None:
            result = PageClassification(
                document_type=previous_type,
                confidence=continuation_confidence,
                scores=result.scores,
            )
        if result.document_type != UNKNOWN:
            previous_type = result.document_type
        classifications.append(result)

    segments: list[SegmentSpec] = []
    for page_index, result in enumerate(classifications):
        if segments and segments[-1].document_type == result.document_type:
            current = segments[-1]
            segments[-1] = replace(
                current,
                page_end=page_index,
                confidence=min(current.confidence, result.confidence),
            )
            continue
        segments.append(
            SegmentSpec(
                page_start=page_index,
                page_end=page_index,
                document_type=result.document_type,
                confidence=result.confidence,
                fingerprint=layout_fingerprint(texts[page_index]),
            )
        )

    validate_coverage(segments, len(texts))
    return tuple(segments)


def validate_coverage(
    segments: list[SegmentSpec] | tuple[SegmentSpec, ...], page_count: int
) -> None:
    """Raise when segments do not cover ``0..page_count-1`` exactly once."""

    expected = 0
    for segment in segments:
        if segment.document_type not in (*RECOGNISED_TYPES, UNKNOWN):
            raise ValueError(f"unrecognised document type {segment.document_type!r}")
        if segment.page_end < segment.page_start:
            raise ValueError(
                f"segment {segment.page_start}-{segment.page_end} ends before it starts"
            )
        if segment.page_start != expected:
            raise ValueError(
                f"segment {segment.page_start}-{segment.page_end} leaves a gap or "
                f"overlap at page {expected}"
            )
        expected = segment.page_end + 1
    if expected != page_count:
        raise ValueError(f"segments cover {expected} pages but the file has {page_count}")


def layout_fingerprint(text: str) -> str:
    """Stable fingerprint of a page's line skeleton.

    Digit runs become ``#`` and whitespace is collapsed so the same template with
    different values matches. The first page of a segment owns the fingerprint.
    """

    skeleton_lines = []
    for line in text.splitlines():
        normalised = _DIGITS.sub("#", line)
        normalised = _WHITESPACE.sub(" ", normalised).strip().casefold()
        if normalised:
            skeleton_lines.append(normalised)
    skeleton = "\n".join(skeleton_lines)
    return hashlib.sha256(skeleton.encode("utf-8")).hexdigest()
