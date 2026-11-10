from trade_extract.config import Settings
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput, TextCharacter
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.schemas.registry import get_schema
from trade_extract.services.extraction import extract
from trade_extract.services.local_extraction import evidence_lines, extract_local
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


def test_complete_conventional_invoice_uses_local_fast_path_without_provider():
    page = _page(
        [
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
    )
    source = _source()
    provider = MockProvider()
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        settings=Settings(provider="mock"),
    )
    assert result.status == "extracted"
    assert not provider.requests
    assert result.document.fields["invoice.number"].value == "INV-2048"
    assert result.document.fields["totals.payable_amount"].value == "1392.00"
    assert all(
        field.provenance.document_hash == source.document_hash
        for field in result.document.fields.values()
    )
    assert "provider_skipped_local_complete" in result.diagnostics


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
        settings=Settings(provider="mock"),
    )
    assert result.status == "extracted"
    assert result.document.status == "review_required"
    assert result.document.fields["invoice.number"].value == "INV-2048"
    assert "partial_evidence_retained" in result.diagnostics
    assert len(provider.requests) == 2
    assert "invoice.number" not in provider.requests[0].schema_json["allowed_scalar_paths"]
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
