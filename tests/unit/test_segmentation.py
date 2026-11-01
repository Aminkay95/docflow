from __future__ import annotations

import pytest

from trade_extract.services.segmentation import (
    CONTINUATION_CONFIDENCE,
    RECOGNISED_TYPES,
    UNKNOWN,
    SegmentSpec,
    classify_page,
    layout_fingerprint,
    segment_pages,
    validate_coverage,
)

SAMPLES = {
    "commercial_invoice": "COMMERCIAL INVOICE\nInvoice No: INV-2026-00417",
    "proforma_invoice": "PROFORMA INVOICE\nValid until 2026-12-01",
    "bill_of_lading": "BILL OF LADING\nShipper: ACME\nConsignee: Beta",
    "delivery_order": "DELIVERY ORDER\nRelease reference: REL-1",
    "port_pass": "PORT PASS\nVehicle: KAA 123X",
    "custom_entry": "CUSTOMS ENTRY\nEntry No: C-2026-118422",
    "verification_memo": "VERIFICATION MEMO\nSeal intact",
    "packing_list": "PACKING LIST\nGross weight: 18,450.00",
}


@pytest.mark.parametrize("document_type", RECOGNISED_TYPES)
def test_each_recognised_type_is_classified_from_its_text(document_type: str) -> None:
    result = classify_page(SAMPLES[document_type])
    assert result.document_type == document_type
    assert 0.0 <= result.confidence <= 1.0


def test_classification_never_reads_a_filename() -> None:
    # The classifier accepts text only; a file called invoice.pdf cannot tilt it.
    assert classify_page("BILL OF LADING\nShipper: ACME").document_type == "bill_of_lading"


def test_a_tie_is_unknown() -> None:
    result = classify_page("BILL OF LADING\nPACKING LIST")
    assert result.document_type == UNKNOWN
    assert result.confidence == pytest.approx(0.5)


def test_a_score_below_the_floor_is_unknown() -> None:
    result = classify_page(
        "COMMERCIAL INVOICE\nPROFORMA INVOICE\nTax Invoice\nPacking List",
        min_confidence=0.9,
    )
    assert result.document_type == UNKNOWN
    assert result.confidence < 0.9


def test_one_weak_body_signal_is_unknown() -> None:
    result = classify_page("DUTY")
    assert result.document_type == UNKNOWN
    assert result.scores[0][1] < 0.55
    # The share is 1.0 (no competitor), but weak absolute evidence must not
    # surface as a confident classification.
    assert result.confidence == 0.0


def test_no_text_is_unknown() -> None:
    result = classify_page("   \n\n")
    assert result.document_type == UNKNOWN
    assert result.confidence == 0.0


def test_confidence_is_always_bounded() -> None:
    for text in SAMPLES.values():
        confidence = classify_page(text).confidence
        assert 0.0 <= confidence <= 1.0


def test_mixed_file_yields_three_non_overlapping_segments() -> None:
    pages = [
        "BILL OF LADING",
        "COMMERCIAL INVOICE",
        "PACKING LIST",
    ]
    segments = segment_pages(pages)
    assert [(s.page_start, s.page_end, s.document_type) for s in segments] == [
        (0, 0, "bill_of_lading"),
        (1, 1, "commercial_invoice"),
        (2, 2, "packing_list"),
    ]
    validate_coverage(segments, len(pages))


def test_multipage_type_is_one_contiguous_segment() -> None:
    pages = [
        "BILL OF LADING",
        "COMMERCIAL INVOICE",
        "Line item 1\nWidget 10.00",
        "PACKING LIST",
    ]
    segments = segment_pages(pages)
    assert [(s.page_start, s.page_end, s.document_type) for s in segments] == [
        (0, 0, "bill_of_lading"),
        (1, 2, "commercial_invoice"),
        (3, 3, "packing_list"),
    ]


def test_continuation_inherits_and_merges_into_the_previous_segment() -> None:
    pages = [
        "COMMERCIAL INVOICE",
        "Line item 1\nWidget 10.00",
        "PACKING LIST",
    ]
    segments = segment_pages(pages)
    assert [(s.page_start, s.page_end, s.document_type) for s in segments] == [
        (0, 1, "commercial_invoice"),
        (2, 2, "packing_list"),
    ]
    assert segments[0].confidence == pytest.approx(CONTINUATION_CONFIDENCE)


def test_no_text_file_is_one_unknown_segment_covering_every_page() -> None:
    segments = segment_pages(["", ""])
    assert len(segments) == 1
    assert segments[0].document_type == UNKNOWN
    assert (segments[0].page_start, segments[0].page_end) == (0, 1)


def test_leading_ambiguous_pages_stay_unknown_until_a_confident_page() -> None:
    pages = ["", "BILL OF LADING\nPACKING LIST", "COMMERCIAL INVOICE"]
    segments = segment_pages(pages)
    assert [(s.page_start, s.page_end, s.document_type) for s in segments] == [
        (0, 1, UNKNOWN),
        (2, 2, "commercial_invoice"),
    ]


def test_every_page_is_covered_exactly_once() -> None:
    pages = [""] + [SAMPLES[t] for t in RECOGNISED_TYPES] + [""]
    segments = segment_pages(pages)
    covered = [
        page_index
        for segment in segments
        for page_index in range(segment.page_start, segment.page_end + 1)
    ]
    assert covered == list(range(len(pages)))


def test_coverage_validator_rejects_a_gap() -> None:
    segments = [
        SegmentSpec(0, 0, "commercial_invoice", 1.0, "a"),
        SegmentSpec(2, 2, "packing_list", 1.0, "b"),
    ]
    with pytest.raises(ValueError, match="gap or overlap"):
        validate_coverage(segments, 3)


def test_coverage_validator_rejects_an_overlap() -> None:
    segments = [
        SegmentSpec(0, 1, "commercial_invoice", 1.0, "a"),
        SegmentSpec(1, 2, "packing_list", 1.0, "b"),
    ]
    with pytest.raises(ValueError, match="gap or overlap"):
        validate_coverage(segments, 3)


def test_coverage_validator_rejects_incomplete_coverage() -> None:
    with pytest.raises(ValueError, match="file has 3"):
        validate_coverage([SegmentSpec(0, 1, "commercial_invoice", 1.0, "a")], 3)


def test_fingerprint_ignores_changing_values() -> None:
    first = layout_fingerprint("INVOICE NO 12345\nTOTAL 1,250.00\nDATE 2026-01-02")
    second = layout_fingerprint("INVOICE NO 99999\nTOTAL 8,999.10\nDATE 2026-11-30")
    assert first == second


def test_fingerprint_changes_with_the_template() -> None:
    assert layout_fingerprint("INVOICE\nTOTAL 1.00") != layout_fingerprint(
        "PACKING LIST\nTOTAL 1.00"
    )
