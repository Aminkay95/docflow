from dataclasses import replace
from pathlib import Path

import pytest

from tests.support import make_pdf
from trade_extract.domain.conditioning import VERSION, CoordinateTransform
from trade_extract.domain.fields import Agreement, Field
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import PageImage, ProviderResult
from trade_extract.domain.provenance import BBox, CoordinateSpace, Provenance, ReaderKind
from trade_extract.domain.readers import FieldCandidate, PageFeatures, PageInput
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.schemas.registry import get_schema
from trade_extract.services.extraction import extract, merge_candidates
from trade_extract.services.page_model import render_container


def fixture(tmp_path: Path):
    spec = get_schema("commercial_invoice")
    specs = [s for s in spec.model.field_set.specs if s.required and not s.indexed]
    values = ["INV-001", "2026-11-03", "USD", "Seller", "Buyer", "10", "10", "10", "10"]
    path = tmp_path / "invoice.pdf"
    path.write_bytes(make_pdf([values]))
    rendered = next(
        render_container(
            path, "application/pdf", document="test", dpi=72, thumbnail_max_px=128, quality=85
        )
    )
    source = EnvelopeSource(
        filename="invoice.pdf",
        content_type="application/pdf",
        file_hash="a" * 64,
        document_hash=document_hash(
            source_file_sha256="a" * 64,
            page_start=0,
            page_end=0,
            document_type="commercial_invoice",
        ),
        page_start=0,
        page_end=0,
    )
    page = PageInput(
        0,
        PageImage(0, rendered.image_bytes, rendered.width_px, rendered.height_px),
        rendered.characters,
        conditioning_version=VERSION,
        conditioning_status="conditioned",
        transform=CoordinateTransform(
            (rendered.width_px, rendered.height_px), (rendered.width_px, rendered.height_px)
        ),
    )

    def payload(reader):
        fields = {}
        for field_spec, value in zip(specs, values, strict=True):
            start = page.text.index(value)
            chars = page.characters[start : start + len(value)]
            bbox = BBox(
                x0=min(c.bbox.x0 for c in chars),
                y0=min(c.bbox.y0 for c in chars),
                x1=max(c.bbox.x1 for c in chars),
                y1=max(c.bbox.y1 for c in chars),
            )
            fields[field_spec.path] = Field(
                kind="scalar",
                value=value,
                value_type=field_spec.value_type,
                raw_text=value,
                agreement=Agreement.SINGLE_READER,
                provenance=Provenance(
                    page_index=0,
                    bbox=bbox,
                    coordinate_space=CoordinateSpace.NORMALIZED_TOP_LEFT,
                    reader=reader,
                    document_hash=source.document_hash,
                ),
            ).model_dump(mode="json")
        return {"source": source.model_dump(), "fields": fields}

    return source, page, payload


def test_sufficient_boxed_digital_text_skips_image_readers(tmp_path):
    source, page, payload = fixture(tmp_path)
    provider = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult(payload(ReaderKind.TEXT_LAYER), "mock-text"),
                ProviderResult(payload(ReaderKind.VISION), "mock-vision"),
            ]
        }
    )
    result = extract(provider, document_type="commercial_invoice", source=source, pages=(page,))
    assert result.status == "extracted", result.errors
    assert len(result.candidates) == 9
    assert {candidate.field.provenance.model for candidate in result.candidates} == {"mock-text"}
    assert all(f.agreement == Agreement.SINGLE_READER for f in result.document.fields.values())
    assert not provider.requests[0].pages
    assert len(provider.requests) == 1


def test_bounded_repair_preserves_original_evidence(tmp_path):
    source, page, payload = fixture(tmp_path)
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult("{", "mock")]}
    )
    result = extract(provider, document_type="commercial_invoice", source=source, pages=(page,))
    assert result.status == "extraction_failed" and result.document is None
    assert len(provider.requests) == 2
    assert provider.requests[1].evidence == provider.requests[0].evidence
    assert provider.requests[1].repair_errors
    repaired = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult("{", "mock"),
                ProviderResult(payload(ReaderKind.TEXT_LAYER), "mock"),
                ProviderResult(payload(ReaderKind.VISION), "mock"),
            ]
        }
    )
    assert (
        extract(repaired, document_type="commercial_invoice", source=source, pages=(page,)).status
        == "extracted"
    )


def test_source_tampering_and_unreadable_are_explicit(tmp_path):
    source, page, payload = fixture(tmp_path)
    raw = payload(ReaderKind.TEXT_LAYER)
    raw["source"]["filename"] = "tampered.pdf"
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    assert (
        extract(provider, document_type="commercial_invoice", source=source, pages=(page,)).document
        is None
    )
    assert (
        extract(
            provider,
            document_type="commercial_invoice",
            source=source,
            pages=(replace(page, unreadable=True),),
        ).status
        == "unreadable"
    )


def test_merge_is_order_stable_and_duplicates_are_not_votes(tmp_path):
    _, _, payload = fixture(tmp_path)
    text = Field.model_validate(payload(ReaderKind.TEXT_LAYER)["fields"]["invoice.number"])
    vision = Field.model_validate(payload(ReaderKind.VISION)["fields"]["invoice.number"])
    a = FieldCandidate("invoice.number", text)
    b = FieldCandidate("invoice.number", vision)
    assert merge_candidates([a, a])["invoice.number"].agreement == Agreement.SINGLE_READER
    assert merge_candidates([a, b]) == merge_candidates([b, a])
    b = replace(b, field=b.field.model_copy(update={"value": "different"}))
    merged = merge_candidates([b, a, a])["invoice.number"]
    assert merged.agreement == Agreement.DISPUTED
    assert len(merged.alternatives) == 1


def test_missing_text_only_calls_vision_and_keeps_disagreement(tmp_path):
    source, page, payload = fixture(tmp_path)
    provider = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult(payload(ReaderKind.VISION), "mock")
            ]
        }
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(replace(page, characters=()),),
    )
    assert result.status == "extracted"
    assert len(provider.requests) == 1
    assert all(f.agreement == Agreement.SINGLE_READER for f in result.document.fields.values())
    vision = payload(ReaderKind.VISION)
    vision["fields"]["invoice.number"]["value"] = "INV-002"
    provider = MockProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult(payload(ReaderKind.TEXT_LAYER), "mock"),
                ProviderResult(vision, "mock"),
            ]
        }
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(replace(page, features=PageFeatures(photograph=True)),),
    )
    field = result.document.fields["invoice.number"]
    assert field.agreement == Agreement.DISPUTED
    assert {field.value, field.alternatives[0].value} == {"INV-001", "INV-002"}


def test_forged_text_box_and_truncated_table_trigger_repair(tmp_path):
    source, page, payload = fixture(tmp_path)
    bad_table = payload(ReaderKind.TEXT_LAYER)
    bad_table["tables"] = {
        "line_items": {"row_count_stated": 1, "row_count_extracted": 0, "truncated": True}
    }
    for raw in (payload(ReaderKind.TEXT_LAYER), bad_table):
        if not raw.get("tables"):
            raw["fields"]["invoice.number"]["provenance"]["bbox"] = {
                "x0": 0,
                "y0": 0,
                "x1": 1,
                "y1": 1,
            }
        provider = MockProvider(
            {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
        )
        result = extract(provider, document_type="commercial_invoice", source=source, pages=(page,))
        assert result.status == "extraction_failed"
        assert len(provider.requests) == 2


def test_repairs_keep_paths_and_guard_codes_without_values(tmp_path):
    source, page, payload = fixture(tmp_path)
    raw = payload(ReaderKind.TEXT_LAYER)
    raw["fields"]["invoice.number"]["provenance"].pop("bbox")
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    extract(provider, document_type="commercial_invoice", source=source, pages=(page,))
    errors = provider.requests[1].repair_errors
    assert "fields.invoice.number.provenance.bbox:missing" in errors
    assert "INV-001" not in str(errors)
    raw = payload(ReaderKind.TEXT_LAYER)
    raw["source"]["filename"] = "private filename"
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    extract(provider, document_type="commercial_invoice", source=source, pages=(page,))
    assert provider.requests[1].repair_errors == ("source:source_identity_mismatch",)


def test_service_owns_lifecycle_metadata(tmp_path):
    source, page, payload = fixture(tmp_path)
    raw = payload(ReaderKind.VISION)
    raw.update(
        status="resolved", created_at="2020-01-01T00:00:00Z", extracted_at="2020-01-01T00:00:00Z"
    )
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(replace(page, characters=()),),
    )
    assert result.document.status == "extracted"
    assert result.document.created_at is result.document.extracted_at is None


def test_reader_evidence_identity_and_available_image_pages(tmp_path):
    source, page, payload = fixture(tmp_path)
    provider = MockProvider()
    with pytest.raises(ValueError, match="image_page_index_mismatch"):
        extract(
            provider,
            document_type="commercial_invoice",
            source=source,
            pages=(replace(page, image=replace(page.image, page_index=1)),),
        )
    from trade_extract.services.readers import VisionReader

    raw = payload(ReaderKind.VISION)
    # Page is readable, but the vision reader only received page 1's image.
    source = source.model_copy(
        update={
            "page_end": 1,
            "document_hash": document_hash(
                source_file_sha256=source.file_hash,
                page_start=0,
                page_end=1,
                document_type="commercial_invoice",
            ),
        }
    )
    raw["source"] = source.model_dump()
    for field in raw["fields"].values():
        field["provenance"]["document_hash"] = source.document_hash
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(
            replace(page, image=None),
            replace(page, page_index=1, image=replace(page.image, page_index=1)),
        ),
        readers=(VisionReader(),),
    )
    assert result.status == "extraction_failed"
    assert "reader_provenance_mismatch" in str(result.errors)
    assert 'Required provenance.reader: "vision"' in provider.requests[0].evidence


def test_value_cannot_be_a_numeric_substring_of_citation(tmp_path):
    source, page, payload = fixture(tmp_path)
    raw = payload(ReaderKind.TEXT_LAYER)
    # Full INV-001 box and citation cannot justify emitting INV-00.
    raw["fields"]["invoice.number"]["value"] = "INV-00"
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    result = extract(provider, document_type="commercial_invoice", source=source, pages=(page,))
    assert result.status == "extraction_failed"
    assert "value_span_geometry_mismatch" in str(result.errors)


def test_long_decimals_and_metadata_ties_are_order_independent(tmp_path):
    _, _, payload = fixture(tmp_path)
    text = Field.model_validate(payload(ReaderKind.TEXT_LAYER)["fields"]["totals.payable_amount"])
    vision = Field.model_validate(payload(ReaderKind.VISION)["fields"]["totals.payable_amount"])
    text = text.model_copy(update={"value": "123456789012345678901234567890.01"})
    vision = vision.model_copy(update={"value": "123456789012345678901234567890.02"})
    assert (
        merge_candidates([FieldCandidate("x", text), FieldCandidate("x", vision)])["x"].agreement
        == Agreement.DISPUTED
    )
    a = FieldCandidate("x", text)
    b = FieldCandidate("x", text.model_copy(update={"raw_text": "different metadata"}))
    assert merge_candidates([a, b]) == merge_candidates([b, a])


def test_mixed_unreadable_pages_keep_reasons_and_cannot_promote_document(tmp_path):
    source, page, payload = fixture(tmp_path)
    source = source.model_copy(
        update={
            "page_end": 1,
            "document_hash": document_hash(
                source_file_sha256=source.file_hash,
                page_start=0,
                page_end=1,
                document_type="commercial_invoice",
            ),
        }
    )
    raw = payload(ReaderKind.VISION)
    for field in raw["fields"].values():
        field["provenance"]["document_hash"] = source.document_hash
    raw["source"] = source.model_dump()
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "mock")]}
    )
    bad = replace(
        page,
        page_index=1,
        image=replace(page.image, page_index=1),
        conditioning_status="unreadable",
        unreadable_reasons=("blurred",),
    )
    from trade_extract.services.readers import VisionReader

    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page, bad),
        readers=(VisionReader(),),
    )
    assert result.status == "unreadable" and result.document is None
    assert result.errors == ("page:1:blurred",)
    assert [image.page_index for image in provider.requests[0].pages] == [0]


@pytest.mark.parametrize("failure", ["no_evidence", "provider", "validation"])
def test_mixed_refusal_reasons_survive_early_exits(tmp_path, failure):
    from trade_extract.domain.inference import ProviderError
    from trade_extract.services.readers import VisionReader

    source, page, _ = fixture(tmp_path)
    source = source.model_copy(update={"page_end": 1})
    bad = replace(
        page,
        page_index=1,
        image=replace(page.image, page_index=1),
        conditioning_status="unreadable",
        unreadable_reasons=("blurred",),
    )

    class FailingProvider(MockProvider):
        def vision_extract(self, request):
            if failure == "provider":
                raise ProviderError("provider_unavailable")
            return ProviderResult("{", "mock")

    provider = FailingProvider()
    if failure == "no_evidence":
        page = replace(page, image=None, characters=())
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page, bad),
        readers=(VisionReader(),),
        max_repairs=0,
    )
    assert "page:1:blurred" in result.errors
    if failure == "no_evidence":
        assert result.status == "unreadable" and "no_reader_evidence" in result.errors
    elif failure == "provider":
        assert "provider_unavailable" in result.errors
    else:
        assert "$:invalid_json" in result.errors
    assert result.document is None
