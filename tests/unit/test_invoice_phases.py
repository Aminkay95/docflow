"""Scripted invoice stages: no real provider or OCR engine."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from trade_extract.config import Settings
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import ProviderError, ProviderResult
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import (
    OCREvidence,
    PageFeatures,
    PageInput,
    TableRegion,
    TableRow,
    TextCharacter,
)
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.schemas.registry import get_schema
from trade_extract.services.extraction import extract
from trade_extract.services.local_extraction import evidence_lines
from trade_extract.services.readers import TextLayerReader
from trade_extract.services.readers.base import invoice_phase_schema, partial_model
from trade_extract.services.readers.text_layer import reconstruct_values


def fixture(kind, count=2):
    source = EnvelopeSource(
        filename="invoice.pdf",
        content_type="application/pdf",
        file_hash="a" * 64,
        document_hash=document_hash(
            source_file_sha256="a" * 64, page_start=0, page_end=0, document_type=kind
        ),
        page_start=0,
        page_end=0,
    )
    prefix = "invoice" if kind == "commercial_invoice" else "proforma"
    values = {
        prefix + ".number": "INV123",
        prefix + ".issue_date": "11/Nov/2026",
        prefix + ".currency": "US dollars",
        "seller.name": "Seller",
        "buyer.name": "Buyer",
        "seller.identifier": "TAX123",
        "shipment.container_numbers[0]": "ABCD1234567",
        "totals.line_extension_amount": "22.00",
        "totals.tax_exclusive_amount": "22.00",
        "totals.tax_inclusive_amount": "22.00",
        "totals.payable_amount": "22.00",
        "tax_breakdown[0].tax_rate": "0%",
    }
    chars = []
    citations = {}
    for i, (path, value) in enumerate(values.items()):
        y = 0.01 + i * 0.015
        citations[path] = (value, f"p0l{i}")
        chars.extend(
            TextCharacter(c, BBox(x0=0.01 + j * 0.005, y0=y, x1=0.014 + j * 0.005, y1=y + 0.01))
            for j, c in enumerate(value)
        )
        chars.append(TextCharacter("\n", BBox(x0=0, y0=y, x1=0.001, y1=y + 0.01)))
    rows = []
    for row in range(count):
        y = 0.3 + row * 0.01
        row_values = {
            "description": f"Goods{row}",
            "quantity": "2.00",
            "unit_price": "5.50",
            "line_extension_amount": "11.00",
            "item_code": f"SKU{row}",
        }
        text = " ".join(row_values.values())
        line_index = len(values) + row
        for key, value in row_values.items():
            citations[f"line_items[{row}].{key}"] = (value, f"p0l{line_index}")
        chars.extend(
            TextCharacter(c, BBox(x0=0.01 + j * 0.01, y0=y, x1=0.019 + j * 0.01, y1=y + 0.005))
            for j, c in enumerate(text)
        )
        chars.append(TextCharacter("\n", BBox(x0=0, y0=y, x1=0.001, y1=y + 0.005)))
        rows.append(TableRow(BBox(x0=0, y0=y, x1=0.99, y1=y + 0.006)))
    region = TableRegion(
        "r0", BBox(x0=0, y0=0.29, x1=1, y1=0.31 + count * 0.01), tuple(rows), "line_items"
    )
    page = PageInput(
        0,
        characters=tuple(chars),
        features=PageFeatures(good_text=True, dense_table=False),
        ocr=OCREvidence(0, "ok", tables=(region,)),
    )
    return source, page, citations


class Scripted:
    name = "scripted"

    def __init__(self, citations, missing=None, fail=None):
        self.citations = citations
        self.missing = missing
        self.fail = fail
        self.requests = []

    def text_reason(self, request):
        self.requests.append(request)
        if request.stage == self.fail:
            raise ProviderError("scripted_timeout")
        allowed = request.schema_json["allowed_scalar_paths"]
        return ProviderResult(
            {
                "values": [
                    {"path": p, "value": v, "evidence_id": e}
                    for p, (v, e) in self.citations.items()
                    if p in allowed and p != self.missing
                ]
            },
            "scripted",
        )


@pytest.mark.parametrize("kind", ["commercial_invoice", "proforma_invoice"])
def test_small_digital_invoice_captures_optional_cells_and_arrays(kind):
    source, page, citations = fixture(kind)
    provider = Scripted(citations)
    result = extract(
        provider,
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors
    assert result.document is not None, result.errors
    assert result.document.status == "extracted", result.errors
    assert result.document.fields["line_items[1].item_code"].value == "SKU1"
    assert result.document.fields["seller.identifier"].value == "TAX123"
    assert result.document.fields["tax_breakdown[0].tax_rate"].value == "0%"
    assert result.document.tables["line_items"].row_count_extracted == 2
    assert [r.stage for r in provider.requests] == [
        "invoice_header_party",
        "invoice_line_items",
        "invoice_totals_tax",
    ]
    assert not result.routes[0].table_required
    assert (
        "proforma.valid_until"
        in invoice_phase_schema("proforma_invoice", "header_party")["allowed_scalar_paths"]
    )
    assert (
        "bill_of_lading.number"
        not in invoice_phase_schema("proforma_invoice", "header_party")["allowed_scalar_paths"]
    )


@pytest.mark.parametrize("kind", ["commercial_invoice", "proforma_invoice"])
@pytest.mark.parametrize("failure", ["cell", "structure", "provider"])
def test_incomplete_evidence_survives_for_review(kind, failure):
    source, page, citations = fixture(kind)
    if failure == "structure":
        page = replace(page, ocr=None)
    provider = Scripted(
        citations,
        missing="line_items[0].quantity" if failure == "cell" else None,
        fail="invoice_totals_tax" if failure == "provider" else None,
    )
    result = extract(
        provider,
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
    )
    assert result.document is not None, result.errors
    assert result.document.status == "review_required"
    assert result.document.fields["line_items[0].description"].value == "Goods0"
    assert result.errors
    assert "partial_evidence_retained" in result.diagnostics
    if failure == "cell":
        with pytest.raises(ValidationError):
            get_schema(kind).model.model_validate(result.document.model_dump())


def test_long_table_is_bounded_and_has_global_indices():
    source, page, citations = fixture("commercial_invoice", 40)
    provider = Scripted(citations)
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
        settings=Settings(provider_max_images=5),
    )
    assert result.document is not None, result.errors
    assert result.document.status == "extracted", result.errors
    rows = [r for r in provider.requests if r.stage == "invoice_line_items"]
    assert len(rows) == 10
    assert all(len(r.schema_json["allowed_row_indices"]) <= 4 for r in rows)
    assert result.document.tables["line_items"].row_count_stated == 40


@pytest.mark.parametrize("bad", ["unknown", "duplicate", "outside"])
def test_phase_rejects_bad_citations_or_paths(bad):
    source, page, citations = fixture("commercial_invoice")
    from trade_extract.domain.inference import InferenceRequest

    request = InferenceRequest(
        "commercial_invoice",
        source.document_hash,
        invoice_phase_schema("commercial_invoice", "line_items", rows=(0,)),
        "",
        source_json=source.model_dump(),
    )
    value, evidence = citations["line_items[0].description"]
    item = {
        "path": "line_items[1].description" if bad == "outside" else "line_items[0].description",
        "value": value,
        "evidence_id": "unknown" if bad == "unknown" else evidence,
    }
    with pytest.raises(ValueError, match="invalid_compact_provider_"):
        reconstruct_values(
            ProviderResult({"values": [item, item] if bad == "duplicate" else [item]}, "scripted"),
            request,
            evidence_lines((page,)),
            ReaderKind.TEXT_LAYER,
        )


def test_partial_row_is_not_a_complete_row():
    assert get_schema("commercial_invoice").model.field_set.resolve("line_items[0].item_code")
    assert (
        not partial_model("commercial_invoice").field_set.resolve("line_items[0].quantity").required
    )
    assert (
        get_schema("commercial_invoice").model.field_set.resolve("line_items[0].quantity").required
    )


@pytest.mark.parametrize("kind", ["commercial_invoice", "proforma_invoice"])
@pytest.mark.parametrize("failure", ["missing_rows", "truncated", "budget"])
def test_invoice_never_completes_with_missing_rows_or_budget_failure(kind, failure):
    source, page, citations = fixture(kind)
    if failure == "missing_rows":
        citations = {p: v for p, v in citations.items() if not p.startswith("line_items[")}

    class Provider(Scripted):
        def text_reason(self, request):
            result = super().text_reason(request)
            if failure == "truncated" and request.stage == "invoice_line_items":
                return replace(result, finish_reason="length")
            return result

    provider = Provider(citations)
    result = extract(
        provider,
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
        settings=Settings(provider_max_calls_per_page=1 if failure == "budget" else 40),
    )
    assert result.document.status == "review_required", result.errors
    assert result.errors
    assert result.document.tables["line_items"].row_count_stated == 2
    if failure == "budget":
        assert len(provider.requests) == 1
    if failure == "missing_rows":
        assert result.document.tables["line_items"].unresolved_rows == [0, 1]


def conditioned(page):
    import io

    from PIL import Image

    from trade_extract.domain.conditioning import VERSION, CoordinateTransform
    from trade_extract.domain.inference import PageImage

    out = io.BytesIO()
    Image.new("RGB", (1000, 1000), "white").save(out, format="PNG")
    return replace(
        page,
        image=PageImage(0, out.getvalue(), 1000, 1000),
        conditioning_version=VERSION,
        conditioning_status="conditioned",
        transform=CoordinateTransform((1000, 1000), (1000, 1000)),
    )


@pytest.mark.parametrize("kind", ["commercial_invoice", "proforma_invoice"])
def test_ocr_invoice_cells_keep_whole_span_provenance(kind):
    from trade_extract.domain.readers import OCRSpan
    from trade_extract.services.readers.paddle import PaddleReader

    source, page, citations = fixture(kind)
    lines = {line.evidence_id: line for line in evidence_lines((page,))}
    spans = []
    ocr_citations = {}
    for path, (value, evidence_id) in citations.items():
        line = lines[evidence_id]
        text = "".join(c.text for c in line.characters)
        start = text.index(value)
        box = line.box_for(start, start + len(value))
        ocr_citations[path] = (value, f"p0l{len(spans)}")
        spans.append(OCRSpan(value, box))
    page = conditioned(
        replace(
            page, characters=(), features=PageFeatures(), ocr=replace(page.ocr, spans=tuple(spans))
        )
    )
    result = extract(
        Scripted(ocr_citations),
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(PaddleReader(),),
        max_repairs=0,
    )
    assert result.document is not None, result.errors
    assert result.document.status == "extracted", result.errors
    assert all(
        field.provenance.reader == ReaderKind.PADDLE for field in result.document.fields.values()
    )
    assert result.document.fields["line_items[0].item_code"].value == "SKU0"


@pytest.mark.parametrize("kind", ["commercial_invoice", "proforma_invoice"])
@pytest.mark.parametrize("scenario", ["normal", "length", "max_tokens", "crop_budget"])
def test_vision_phases_use_row_crops_and_page_arrays(kind, scenario):
    import json
    import re

    from trade_extract.domain.fields import Field
    from trade_extract.domain.provenance import Provenance
    from trade_extract.services.readers import VisionReader

    source, page, citations = fixture(kind)
    lines = {line.evidence_id: line for line in evidence_lines((page,))}

    class Provider:
        name = "vision-scripted"

        def vision_extract(self, request):
            metadata = json.loads(request.evidence.split("Attached images: ")[-1])
            fields = {}
            for path in request.schema_json["allowed_phase_paths"]:
                if path not in citations:
                    continue
                value, evidence = citations[path]
                line = lines[evidence]
                text = "".join(c.text for c in line.characters)
                start = text.index(value)
                box = line.box_for(start, start + len(value))
                match = re.match(r"line_items\[(\d+)\]", path)
                if match and scenario != "crop_budget":
                    crop = next(m for m in metadata if m.get("row_index") == int(match.group(1)))
                    left, top, right, bottom = crop["crop_rectangle"]
                    box = BBox(
                        x0=(box.x0 * 1000 - left) / (right - left),
                        y0=(box.y0 * 1000 - top) / (bottom - top),
                        x1=(box.x1 * 1000 - left) / (right - left),
                        y1=(box.y1 * 1000 - top) / (bottom - top),
                    )
                fields[path] = Field(
                    kind="scalar",
                    value=value,
                    agreement="single_reader",
                    value_type=get_schema(kind).model.field_set.resolve(path).value_type,
                    provenance=Provenance(
                        page_index=0,
                        bbox=box,
                        reader=ReaderKind.VISION,
                        coordinate_space="normalized_top_left",
                        document_hash=source.document_hash,
                    ),
                ).model_dump(mode="json")
            return ProviderResult(
                {"source": source.model_dump(), "fields": fields},
                "vision-scripted",
                finish_reason=scenario
                if request.stage == "invoice_line_items" and scenario in {"length", "max_tokens"}
                else None,
            )

    page = conditioned(replace(page, characters=(), features=PageFeatures()))
    result = extract(
        Provider(),
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(VisionReader(),),
        max_repairs=0,
        settings=Settings(table_max_crops=1) if scenario == "crop_budget" else Settings(),
    )
    assert result.document is not None, result.errors
    assert result.document.status == ("extracted" if scenario == "normal" else "review_required"), (
        result.errors
    )
    if scenario in {"length", "max_tokens"}:
        assert "stage_output_truncated" in result.errors
    if scenario == "crop_budget":
        assert "table_crop_budget_exhausted" in result.errors
    assert result.document.fields["shipment.container_numbers[0]"].value == "ABCD1234567"
    assert result.document.fields["line_items[1].item_code"].value == "SKU1"


def test_verified_empty_companion_page_does_not_hide_segment_rows():
    source, page, citations = fixture("commercial_invoice")
    source = source.model_copy(
        update={
            "page_end": 1,
            "document_hash": document_hash(
                source_file_sha256="a" * 64,
                page_start=0,
                page_end=1,
                document_type="commercial_invoice",
            ),
        }
    )
    companion = PageInput(1, ocr=OCREvidence(1, "ok"))
    result = extract(
        Scripted(citations),
        document_type="commercial_invoice",
        source=source,
        pages=(page, companion),
        readers=(TextLayerReader(),),
        max_repairs=0,
    )
    assert result.document.status == "extracted", result.errors
    assert result.document.tables["line_items"].row_count_stated == 2


@pytest.mark.parametrize("kind", ["commercial_invoice", "proforma_invoice"])
@pytest.mark.parametrize("family", ["shipment.container_numbers", "tax_breakdown"])
def test_auxiliary_array_boundary_is_retained_for_review(kind, family):
    source, page, citations = fixture(kind)
    template = family + ("[{}].tax_rate" if family == "tax_breakdown" else "[{}]")
    value = citations[template.format(0)]
    for index in range(32):
        citations[template.format(index)] = value
    result = extract(
        Scripted(citations),
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
    )
    assert result.document.status == "review_required"
    assert "invoice_array_bound_reached" in result.errors
    assert all(result.document.fields[template.format(i)].value == value[0] for i in range(32))


def test_repaired_attempt_does_not_commit_rejected_metadata():
    from copy import deepcopy

    source, page, citations = fixture("commercial_invoice")

    class Reader(TextLayerReader):
        def read(self, provider, request, pages):
            result = super().read(provider, request, pages)
            raw = deepcopy(result.raw_json)
            if request.stage == "invoice_header_party" and not request.repair_errors:
                raw["fields"]["invoice.number"]["provenance"]["reader"] = "vision"
                raw["fields"]["shipment.container_numbers[31]"] = deepcopy(
                    raw["fields"]["seller.name"]
                )
                raw["tables"] = {
                    "line_items": {
                        "row_count_stated": 99,
                        "row_count_extracted": 0,
                        "truncated": True,
                    }
                }
                return replace(result, raw_json=raw, finish_reason="length")
            if request.stage == "invoice_line_items":
                raw["tables"] = {"line_items": {"row_count_stated": 2, "row_count_extracted": 2}}
            return replace(result, raw_json=raw)

    provider = Scripted(citations)
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(Reader(),),
        max_repairs=1,
    )
    assert result.document.status == "extracted", result.errors
    assert not result.errors
    assert len(provider.requests) == 4
    assert "shipment.container_numbers[31]" not in result.document.fields


@pytest.mark.parametrize("unmappable", [False, True])
def test_discovery_remaps_prior_rows_by_physical_geometry(unmappable):
    source, page, citations = fixture("commercial_invoice")
    page = replace(
        page, ocr=replace(page.ocr, tables=(replace(page.ocr.tables[0], table_name=None),))
    )

    class Provider(Scripted):
        def text_reason(self, request):
            if "Independent rows: []" in request.evidence:
                changed = {}
                for path, value in citations.items():
                    path = (
                        path.replace("line_items[0]", "TEMP")
                        .replace("line_items[1]", "line_items[0]")
                        .replace("TEMP", "line_items[1]")
                    )
                    changed[path] = value
                if unmappable:
                    changed["line_items[5].note"] = citations["seller.name"]
                previous = self.citations
                self.citations = changed
                try:
                    return super().text_reason(request)
                finally:
                    self.citations = previous
            return super().text_reason(request)

    result = extract(
        Provider(citations),
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
    )
    assert result.document.status == ("review_required" if unmappable else "extracted"), (
        result.errors
    )
    assert result.document.fields["line_items[0].description"].value == "Goods0"
    assert result.document.fields["line_items[1].description"].value == "Goods1"
    assert all(
        result.document.fields[f"line_items[{i}].description"].agreement.value != "disputed"
        for i in range(2)
    )
    assert "line_items[5].note" not in result.document.fields
    if unmappable:
        assert "pre_discovery_row_unmapped" in result.errors
        assert any(c.field_path == "line_items[5].note" for c in result.candidates)


@pytest.mark.parametrize("reason", ["length", "max_tokens"])
def test_vision_aggregation_keeps_earlier_batch_truncation(reason):
    import json

    from trade_extract.domain.inference import InferenceRequest
    from trade_extract.services.readers import VisionReader

    source, page, _ = fixture("commercial_invoice")
    source = source.model_copy(
        update={
            "page_end": 1,
            "document_hash": document_hash(
                source_file_sha256="a" * 64,
                page_start=0,
                page_end=1,
                document_type="commercial_invoice",
            ),
        }
    )
    first = conditioned(replace(page, characters=()))
    second = replace(first, page_index=1, image=replace(first.image, page_index=1))

    class Provider:
        def vision_extract(self, request):
            return ProviderResult(
                {"source": source.model_dump(), "fields": {}},
                "scripted",
                finish_reason=reason if request.pages[0].page_index == 0 else "stop",
            )

    result = VisionReader().read(
        Provider(),
        InferenceRequest(
            "commercial_invoice",
            source.document_hash,
            {},
            json.dumps({"source": source.model_dump()}),
        ),
        (first, second),
    )
    assert result.finish_reason == reason


def test_text_only_invoice_does_not_need_native_crop_budget():
    source, page, citations = fixture("commercial_invoice")
    result = extract(
        Scripted(citations),
        document_type="commercial_invoice",
        source=source,
        pages=(replace(page, image=None),),
        readers=(TextLayerReader(),),
        max_repairs=0,
        settings=Settings(table_max_crops=1),
    )
    assert result.document.status == "extracted", result.errors
    assert "table_crop_budget_exhausted" not in result.errors
