from dataclasses import replace
from typing import Any

import pytest

from trade_extract.config import Settings
from trade_extract.domain.conditioning import VERSION, CoordinateTransform
from trade_extract.domain.fields import Agreement
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import InferenceRequest, PageImage, ProviderResult
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput, TextCharacter
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.schemas.registry import get_schema
from trade_extract.services.extraction import extract
from trade_extract.services.local_extraction import evidence_lines, extract_local
from trade_extract.services.readers import VisionReader
from trade_extract.services.readers.text_layer import TextLayerReader


def _page(lines: list[str]) -> PageInput:
    characters = []
    for row, text in enumerate(lines):
        for column, char in enumerate(text):
            characters.append(
                TextCharacter(
                    char,
                    BBox(
                        x0=column / 200,
                        y0=row / 30,
                        x1=(column + 1) / 200,
                        y1=(row + 0.6) / 30,
                    ),
                )
            )
    return PageInput(0, characters=tuple(characters), conditioning_status="conditioned")


def _source() -> EnvelopeSource:
    file_hash = "a" * 64
    return EnvelopeSource(
        filename="invoice.pdf",
        content_type="application/pdf",
        file_hash=file_hash,
        document_hash=document_hash(
            source_file_sha256=file_hash,
            page_start=0,
            page_end=0,
            document_type="commercial_invoice",
        ),
        page_start=0,
        page_end=0,
    )


COMPLETE_INVOICE_LINES = [
    "Commercial Invoice No: INV-2048",
    "Invoice Date: 2026-11-04",
    "Currency: USD",
    "Seller: Coastal Exports Ltd",
    "Buyer: Mombasa Trading Ltd",
    "Subtotal: 1200.00",
    "Tax Exclusive Amount: 1200.00",
    "Grand Total: 1392.00",
    "Amount Due: 1392.00",
]


def test_header_only_conventional_invoice_is_inspected_and_reviewed():
    page = _page(COMPLETE_INVOICE_LINES)
    source = _source()
    provider = MockProvider()
    stages = []
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        settings=Settings(provider="mock", llm_extraction_priority=False),
        progress=stages.append,
    )
    assert result.status == "extracted"
    assert [r.stage for r in provider.requests] == [
        "invoice_header_party",
        "invoice_line_items",
        "invoice_totals_tax",
    ]
    assert result.document.status == "review_required"
    assert stages == ["reading", "extracting"]
    assert result.document.fields["invoice.number"].value == "INV-2048"
    assert result.document.fields["totals.payable_amount"].value == "1392.00"
    assert all(
        field.provenance.document_hash == source.document_hash
        for field in result.document.fields.values()
    )
    assert "provider_skipped_local_complete" not in result.diagnostics


def test_ambiguous_duplicate_label_is_not_promoted_locally():
    page = _page(["Invoice No: INV-1", "Invoice No: INV-2"])
    source = _source()
    candidates = extract_local(
        "commercial_invoice",
        get_schema("commercial_invoice"),
        source.document_hash,
        evidence_lines((page,)),
    )
    assert "invoice.number" not in {candidate.field_path for candidate in candidates}


def test_valid_local_stage_survives_failed_targeted_provider_stage():
    page = _page(["Commercial Invoice No: INV-2048"])
    source = _source()
    provider = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult("{", "mock"),
                ProviderResult("{", "mock"),
            ]
        }
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        settings=Settings(provider="mock", llm_extraction_priority=False),
    )
    assert result.status == "extracted"
    assert result.document.status == "review_required"
    assert result.document.fields["invoice.number"].value == "INV-2048"
    assert "partial_evidence_retained" in result.diagnostics
    assert len(provider.requests) == 6
    assert "invoice.number" in provider.requests[0].schema_json["allowed_scalar_paths"]
    assert provider.requests[0].evidence.count("Compact evidence index:") == 1


def test_compact_provider_value_is_expanded_to_verified_provenance():
    page = _page(["INV-2048"])
    source = _source()
    provider = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult(
                    {
                        "values": [
                            {
                                "path": "invoice.number",
                                "value": "INV-2048",
                                "evidence_id": "p0l0",
                            }
                        ],
                        "tables": {},
                    },
                    "mock",
                )
            ]
        }
    )
    result = TextLayerReader().read(
        provider,
        InferenceRequest(
            "commercial_invoice",
            source.document_hash,
            {
                "contract": "evidence_values_v1",
                "allowed_scalar_paths": ["invoice.number"],
            },
            "",
            source_json=source.model_dump(mode="json"),
        ),
        (page,),
    )
    assert result is not None
    field = result.raw_json["fields"]["invoice.number"]
    assert field["value"] == "INV-2048"
    assert field["provenance"]["bbox"] != {"x0": 0, "y0": 0, "x1": 1, "y1": 1}
    assert result.repair_json["values"][0]["evidence_id"] == "p0l0"


def test_ocr_compact_values_keep_only_exact_whole_span_and_stable_identity():
    from dataclasses import replace

    from tests.unit.test_routing_tables_paddle import page as scan_page
    from trade_extract.domain.provenance import ReaderKind
    from trade_extract.services.readers.paddle import PaddleReader

    reader = PaddleReader(
        backend=lambda _: [
            {
                "type": "text",
                "res": [
                    {"text": "INV-2048", "text_region": [[10, 10], [100, 10], [100, 30], [10, 30]]}
                ],
            }
        ]
    )
    scan = scan_page()
    scan = replace(scan, ocr=reader.inspect(scan), characters=_page(["TEXT LAYER"]).characters)
    source = _source()
    request = InferenceRequest(
        "commercial_invoice",
        source.document_hash,
        {"contract": "evidence_values_v1", "allowed_scalar_paths": ["invoice.number"]},
        '{"source":"original","segment_id":"segment"}\nCompact evidence index:\np0l0|TEXT LAYER'
        "\nIndependent rows: []",
        segment_id="segment",
        source_json=source.model_dump(mode="json"),
    )
    for value, evidence_id, expected in [
        ("INV-2048", "p0l0", True),
        ("INV-2048", "missing", False),
        ("invented", "p0l0", False),
        ("2048", "p0l0", False),
    ]:
        provider = MockProvider(
            {
                ("commercial_invoice", source.document_hash): [
                    ProviderResult(
                        {
                            "values": [
                                {
                                    "path": "invoice.number",
                                    "value": value,
                                    "evidence_id": evidence_id,
                                }
                            ],
                            "tables": {},
                        },
                        "mock",
                    )
                ]
            }
        )
        result = reader.read(provider, request, (scan,))
        assert bool(result.raw_json["fields"]) is expected
        transmitted = provider.requests[0]
        assert transmitted.schema_json["contract"] == "evidence_values_v1"
        assert transmitted.segment_id == "segment"
        assert transmitted.source_json == request.source_json
        assert '"source":"original"' in transmitted.evidence
        assert "Independent rows: []" in transmitted.evidence
        assert transmitted.evidence.count("Compact evidence index:") == 1
        assert transmitted.evidence.count("p0l0|") == 1
        assert "TEXT LAYER" not in transmitted.evidence
        assert "INV-2048" in transmitted.evidence
        if expected:
            field = result.raw_json["fields"]["invoice.number"]
            assert field["provenance"]["reader"] == ReaderKind.PADDLE
            assert field["provenance"]["bbox"] == scan.ocr.spans[0].bbox.model_dump()
            assert result.raw_json["source"] == source.model_dump(mode="json")
    other = replace(_page(["another page"]), page_index=1)
    assert evidence_lines((other, scan))[-1].evidence_id == "p0l0"


def test_provider_exhaustion_retains_verified_fields_for_review():
    from trade_extract.domain.inference import ProviderError

    class ExhaustedProvider:
        name = "exhausted"

        def text_reason(self, request):
            raise ProviderError("provider_call_budget_exhausted")

    outcome = extract(
        ExhaustedProvider(),
        document_type="commercial_invoice",
        source=_source(),
        pages=(_page(["Invoice No: INV-2048"]),),
        readers=(TextLayerReader(),),
        settings=Settings(provider="mock"),
    )
    assert outcome.document.status == "review_required"
    assert outcome.document.fields["invoice.number"].value == "INV-2048"
    assert "provider_call_budget_exhausted" in outcome.errors


@pytest.mark.parametrize(
    "value,evidence_id,accepted",
    [
        ("INV-2048", "p0l0", True),
        ("INV-2048", "unknown", False),
        ("invented", "p0l0", False),
        ("2048", "p0l0", False),
    ],
)
def test_ocr_extraction_route_requests_compact_scalar_contract(value, evidence_id, accepted):
    from dataclasses import replace

    from tests.unit.test_routing_tables_paddle import page as scan_page
    from trade_extract.domain.readers import PageFeatures
    from trade_extract.services.readers.paddle import PaddleReader

    reader = PaddleReader(
        backend=lambda _: [
            {
                "type": "text",
                "res": [
                    {"text": "INV-2048", "text_region": [[10, 10], [100, 10], [100, 30], [10, 30]]}
                ],
            }
        ]
    )
    scan = replace(scan_page(), features=PageFeatures(flat_scan=True, dense_table=False))
    provider = MockProvider(
        {
            ("commercial_invoice", _source().document_hash): [
                ProviderResult(
                    {
                        "values": [
                            {"path": "invoice.number", "value": value, "evidence_id": evidence_id}
                        ],
                        "tables": {},
                    },
                    "mock",
                )
            ]
        }
    )
    outcome = extract(
        provider,
        document_type="commercial_invoice",
        source=_source(),
        pages=(scan,),
        readers=(reader,),
        settings=Settings(provider="mock"),
    )
    if accepted:
        assert outcome.document.fields["invoice.number"].value == "INV-2048"
    else:
        assert outcome.status == "extraction_failed" or outcome.document.status == "review_required"
        assert outcome.document is None or "invoice.number" not in outcome.document.fields
    assert provider.requests[0].schema_json["contract"] == "evidence_values_v1"
    assert provider.requests[0].evidence.count("p0l0|") == 1


@pytest.mark.parametrize("reader_kind", ["paddle", "text_layer"])
def test_compact_duplicate_paths_are_rejected_and_envelope_compatibility_is_reader_specific(
    reader_kind,
):
    from trade_extract.domain.provenance import ReaderKind
    from trade_extract.services.readers.text_layer import reconstruct_values

    reader = ReaderKind(reader_kind)
    request = InferenceRequest(
        "commercial_invoice",
        _source().document_hash,
        {"contract": "evidence_values_v1", "allowed_scalar_paths": ["invoice.number"]},
        "",
        source_json=_source().model_dump(mode="json"),
    )
    item = {"path": "invoice.number", "value": "INV-2048", "evidence_id": "p0l0"}
    with pytest.raises(ValueError, match="invalid_compact_provider_duplicate_path"):
        reconstruct_values(
            ProviderResult({"values": [item, item]}, "mock"),
            request,
            evidence_lines((_page(["INV-2048"]),)),
            reader,
        )
    envelope = ProviderResult({"fields": {}, "source": request.source_json}, "mock")
    if reader == ReaderKind.PADDLE:
        with pytest.raises(ValueError, match="invalid_compact_provider_root"):
            reconstruct_values(envelope, request, (), reader)
    else:
        assert reconstruct_values(envelope, request, (), reader) is envelope


def test_llm_priority_calls_provider_and_keeps_local_as_fallback():
    page = _page(COMPLETE_INVOICE_LINES)
    source = _source()
    provider = MockProvider()
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        settings=Settings(provider="mock", llm_extraction_priority=True),
    )
    # The provider runs despite a complete local document...
    assert provider.requests
    assert "mock_fixture_missing" in result.errors
    assert "provider_skipped_local_complete" not in result.diagnostics
    # ...and its failure still falls back to the local candidates.
    assert "partial_evidence_retained" in result.diagnostics
    assert result.status == "extracted"
    assert result.document.status == "review_required"
    assert result.document.fields["invoice.number"].value == "INV-2048"


def test_llm_priority_lets_provider_values_dispute_local_candidates():
    page = _page([*COMPLETE_INVOICE_LINES, "Ref: INV-9999"])
    source = _source()
    provider = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult(
                    {
                        "values": [
                            {
                                "path": "invoice.number",
                                "value": "INV-9999",
                                "evidence_id": "p0l9",
                            }
                        ],
                        "tables": {},
                    },
                    "mock",
                )
            ]
        }
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        settings=Settings(provider="mock", llm_extraction_priority=True),
    )
    assert provider.requests
    assert "provider_skipped_local_complete" not in result.diagnostics
    assert result.status == "extracted", result.errors
    field = result.document.fields["invoice.number"]
    assert field.agreement == Agreement.DISPUTED
    assert {field.value, field.alternatives[0].value} == {"INV-2048", "INV-9999"}
    # Unrelated local fields survive the merge untouched.
    assert result.document.fields["totals.payable_amount"].value == "1392.00"


def _vision_contract(*, priority: bool) -> dict[str, Any]:
    # Incomplete locally (no payable line), so the local-complete bypass never
    # fires and the vision reader is reached in both flag states.
    page = replace(
        _page(COMPLETE_INVOICE_LINES[:-1]),
        image=PageImage(0, b"page-image", 100, 100),
        conditioning_version=VERSION,
        transform=CoordinateTransform((100, 100), (100, 100)),
    )
    source = _source()
    provider = MockProvider()
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(VisionReader(),),
        settings=Settings(provider="mock", llm_extraction_priority=priority),
    )
    assert provider.requests
    assert provider.requests[0].stage == "invoice_header_party"
    assert "mock_fixture_missing" in result.errors
    return provider.requests[0].schema_json


def test_invoice_header_phase_shows_vision_the_full_contract_in_both_priority_modes():
    stripped = _vision_contract(priority=False)
    assert "invoice.number" in stripped["properties"]["fields"]["properties"]
    full = _vision_contract(priority=True)
    assert "invoice.number" in full["properties"]["fields"]["properties"]


def test_pdf_baseline_spaces_preserve_complete_evidence_line():
    from trade_extract.services.readers.text_layer import verify_text_box

    printed = "Example Industrial Supply Company"
    page = _page([printed])
    page = replace(
        page,
        characters=tuple(
            replace(char, bbox=char.bbox.model_copy(update={"y0": 0.025, "y1": 0.02501}))
            if char.text == " "
            else char
            for char in page.characters
        ),
    )
    (line,) = evidence_lines((page,))
    assert line.text == printed
    assert verify_text_box(page, printed, line.bbox)


@pytest.mark.parametrize("printed", ["Nov 9, 2026", "$500.00", "Company, Limited"])
def test_pdf_low_punctuation_stays_in_its_source_line(printed):
    page = _page([printed])
    page = replace(
        page,
        characters=tuple(
            replace(char, bbox=char.bbox.model_copy(update={"y0": 0.018, "y1": 0.02}))
            if char.text in ",."
            else char
            for char in page.characters
        ),
    )
    (line,) = evidence_lines((page,))
    assert line.text == printed


def test_invoice_balance_value_is_read_from_its_labelled_column():
    from trade_extract.services.readers.text_layer import verify_text_box

    label_page = _page(["Balance:"])
    amount_page = _page(["$500.00"])
    amount_page = replace(
        amount_page,
        characters=tuple(
            replace(
                char,
                bbox=char.bbox.model_copy(
                    update={"x0": char.bbox.x0 + 0.3, "x1": char.bbox.x1 + 0.3}
                ),
            )
            for char in amount_page.characters
        ),
    )
    label = evidence_lines((label_page,))[0]
    amount = replace(evidence_lines((amount_page,))[0], evidence_id="p0l1")
    candidates = extract_local(
        "commercial_invoice",
        get_schema("commercial_invoice"),
        _source().document_hash,
        (label, amount),
    )
    balance = next(c.field for c in candidates if c.field_path == "totals.payable_amount")
    assert balance.value == "$500.00"
    assert verify_text_box(amount_page, balance.value, balance.provenance.bbox)
    wrong_row = replace(amount, bbox=amount.bbox.model_copy(update={"y0": 0.2, "y1": 0.22}))
    assert not extract_local(
        "commercial_invoice",
        get_schema("commercial_invoice"),
        _source().document_hash,
        (label, wrong_row),
    )
