"""Story 6 extraction-boundary regressions with real document schemas."""

from dataclasses import replace

import pytest

from tests.unit.test_extraction import fixture
from trade_extract.config import Settings
from trade_extract.domain.fields import Field
from trade_extract.domain.inference import ProviderResult
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import OCREvidence, OCRSpan, PageFeatures, TableRegion, TableRow
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.registry import get_schema
from trade_extract.services.extraction import extract
from trade_extract.services.readers.paddle import PaddleReader


class StaticReader:
    def __init__(self, name, payload):
        self.name, self.payload, self.seen = name, payload, []

    def read(self, provider, request, pages):
        self.seen.append(tuple(p.page_index for p in pages))
        return ProviderResult(self.payload, "injected") if pages else None


def with_rows(payload, count, page_index=0, offset=0, total=None):
    spec = get_schema("commercial_invoice")
    for i in range(count):
        box = BBox(x0=0.1, y0=(i + 0.1) / max(100, count), x1=0.8, y1=(i + 0.9) / max(100, count))
        for cell in spec.model.field_set.required_row_specs("line_items"):
            value = "row" if cell.value_type.value == "string" else "1"
            base = next(iter(payload["fields"].values()))
            payload["fields"][cell.path.replace("[]", f"[{offset + i}]")] = Field.model_validate(
                {
                    **base,
                    "value": value,
                    "raw_text": value,
                    "value_type": cell.value_type,
                    "provenance": {
                        **base["provenance"],
                        "bbox": box.model_dump(),
                        "page_index": page_index,
                    },
                }
            ).model_dump(mode="json")
    total = count if total is None else total
    payload["tables"] = {
        "line_items": {
            "row_count_stated": total,
            "row_count_extracted": count,
            "unresolved_rows": [i for i in range(total) if not offset <= i < offset + count],
        }
    }
    return payload


@pytest.mark.parametrize("bad", [False, True])
def test_paddle_citations_are_guarded_at_extract_boundary(tmp_path, bad):
    source, page, payload = fixture(tmp_path)
    raw = payload(ReaderKind.PADDLE)
    spans = tuple(
        OCRSpan(f["value"], BBox.model_validate(f["provenance"]["bbox"]))
        for f in raw["fields"].values()
    )
    page = replace(
        page,
        characters=(),
        ocr=OCREvidence(0, "ok", spans),
        features=PageFeatures(
            flat_scan=True, photograph=False, skew=False, stamps=False, handwriting=False
        ),
    )
    if bad:
        next(iter(raw["fields"].values()))["value"] = "UNSUPPORTED"
    provider = MockProvider(
        {("commercial_invoice", source.document_hash): [ProviderResult(raw, "ocr-fields")]}
    )
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(PaddleReader(),),
        max_repairs=0,
    )
    assert result.status == ("extraction_failed" if bad else "extracted"), result.errors
    if bad:
        assert "ocr_span_geometry_mismatch" in result.errors[0]


def test_schema_valid_shorter_200_row_table_cannot_pass(tmp_path):
    source, page, payload = fixture(tmp_path)
    # Two physical regions/pages are independently numbered as one semantic table.
    from trade_extract.domain.hashing import document_hash

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
    pages = []
    for index in range(2):
        rows = tuple(
            TableRow(BBox(x0=0.1, y0=i / 100, x1=0.9, y1=(i + 1) / 100)) for i in range(100)
        )
        pages.append(
            replace(
                page,
                page_index=index,
                image=replace(page.image, page_index=index),
                ocr=OCREvidence(
                    index,
                    "ok",
                    tables=(TableRegion("grid", BBox(x0=0, y0=0, x1=1, y1=1), rows, "line_items"),),
                ),
                features=PageFeatures(dense_table=True),
            )
        )
    raw = payload(ReaderKind.VISION)
    raw["source"] = source.model_dump()
    for field in raw["fields"].values():
        field["provenance"]["document_hash"] = source.document_hash
    raw = with_rows(raw, 99)
    # A schema-valid provider claims only 99 rows, despite 200 independent rows.
    get_schema("commercial_invoice").model.model_validate(raw)
    reader = StaticReader(ReaderKind.VISION, raw)
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=tuple(pages),
        readers=(reader, PaddleReader()),
        max_repairs=0,
    )
    assert result.status == "extraction_failed"
    missing = next(e for e in result.errors if "unresolved_rows:" in e)
    assert missing.endswith(",".join(map(str, range(99, 200))))
    assert len(result.inventory.rows) == 200


def test_explicit_reader_exclusion_never_initializes_paddle(tmp_path, monkeypatch):
    source, page, payload = fixture(tmp_path)
    monkeypatch.setattr(PaddleReader, "inspect", lambda *a: pytest.fail("unexpected OCR"))
    reader = StaticReader(ReaderKind.TEXT_LAYER, payload(ReaderKind.TEXT_LAYER))
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(reader,),
    )
    assert result.status == "extracted"


def test_good_digital_page_routes_only_text(tmp_path):
    source, page, payload = fixture(tmp_path)
    page = replace(page, features=PageFeatures(good_text=True, dense_table=False))
    text = StaticReader(ReaderKind.TEXT_LAYER, payload(ReaderKind.TEXT_LAYER))
    vision = StaticReader(ReaderKind.VISION, payload(ReaderKind.VISION))
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(text, vision),
        settings=Settings(routing_min_text_characters=1),
    )
    assert result.status == "extracted"
    assert text.seen == [(0,)] and not vision.seen


@pytest.mark.parametrize("damage", ["value", "raw_text", "bbox"])
def test_paddle_rejects_each_unsupported_citation_part(tmp_path, damage):
    source, page, payload = fixture(tmp_path)
    raw = payload(ReaderKind.PADDLE)
    spans = tuple(
        OCRSpan(f["value"], BBox.model_validate(f["provenance"]["bbox"]))
        for f in raw["fields"].values()
    )
    field = next(iter(raw["fields"].values()))
    if damage == "bbox":
        field["provenance"]["bbox"] = dict(x0=0, y0=0, x1=1, y1=1)
    else:
        field[damage] = "unobserved"
    result = extract(
        MockProvider({("commercial_invoice", source.document_hash): [ProviderResult(raw, "test")]}),
        document_type="commercial_invoice",
        source=source,
        pages=(replace(page, ocr=OCREvidence(0, "ok", spans)),),
        readers=(PaddleReader(),),
        max_repairs=0,
    )
    assert result.status == "extraction_failed"
    assert "ocr_span_geometry_mismatch" in result.errors[0]


@pytest.mark.parametrize("row_count", [25, 200])
def test_native_batches_reconstruct_all_rows_from_anonymous_v3_structure(tmp_path, row_count):
    import json

    from trade_extract.services.readers import VisionReader

    source, p, payload = fixture(tmp_path)
    w, h = p.image.width_px, p.image.height_px

    # Actual PPStructureV3 fields: no application table_name and no region bbox.
    class Backend:
        def predict(self, image):
            yield {
                "res": {
                    "overall_ocr_res": {"rec_texts": [], "rec_polys": []},
                    "table_res_list": [
                        {
                            "pred_html": "<table><thead><tr><th>Item</th></tr></thead>"
                            + "<tr><td>row</td></tr>" * row_count
                            + "</table>",
                            "cell_box_list": [
                                [
                                    0.1 * w,
                                    i * h / (row_count + 1),
                                    0.9 * w,
                                    (i + 1) * h / (row_count + 1),
                                ]
                                for i in range(row_count + 1)
                            ],
                        }
                    ],
                }
            }

    class Provider:
        def __init__(self):
            self.requests = []

        def vision_extract(self, request):
            self.requests.append(request)
            metadata = json.loads(request.evidence.split("Attached images: ")[1])
            crops = metadata[1:]
            indices = [c["row_index"] for c in crops] if crops else list(range(row_count))
            raw = with_rows(
                payload(ReaderKind.VISION), len(indices), offset=indices[0], total=row_count
            )
            for path, field in raw["fields"].items():
                if path.startswith("line_items["):
                    i = int(path.split("[")[1].split("]")[0])
                    field["provenance"]["bbox"] = (
                        dict(x0=0.2, x1=0.8, y0=0.2, y1=0.8)
                        if crops
                        else dict(
                            x0=0.2,
                            x1=0.8,
                            y0=(i + 1.2) / (row_count + 1),
                            y1=(i + 1.8) / (row_count + 1),
                        )
                    )
            assert len(request.pages) <= 8
            assert [m["image_position"] for m in metadata] == list(range(len(request.pages)))
            return ProviderResult(raw, "fixture")

    provider = Provider()
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(replace(p, characters=(), features=PageFeatures(dense_table=True)),),
        readers=(PaddleReader(backend=Backend()), VisionReader()),
        settings=Settings(provider_max_images=8),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors
    assert result.document.tables["line_items"].row_count_extracted == row_count
    assert len(result.inventory.rows) == row_count
    assert len(provider.requests) == 1 + (row_count + 6) // 7


@pytest.mark.parametrize("bad", ["shape", "identity", "field_shape"])
def test_malformed_later_native_batch_enters_bounded_failure(tmp_path, bad):
    from trade_extract.services.readers import VisionReader

    source, p, payload = fixture(tmp_path)
    rows = tuple(TableRow(BBox(x0=0.1, y0=i / 100, x1=0.9, y1=(i + 1) / 100)) for i in range(2))
    p = replace(
        p,
        characters=(),
        features=PageFeatures(dense_table=True),
        ocr=OCREvidence(
            0, "ok", tables=(TableRegion("r", BBox(x0=0, y0=0, x1=1, y1=1), rows, "line_items"),)
        ),
    )

    class Provider:
        calls = 0

        def vision_extract(self, request):
            self.calls += 1
            if self.calls == 2:
                if bad == "shape":
                    return ProviderResult([], "test")
                raw = with_rows(payload(ReaderKind.VISION), 1, offset=1, total=2)
                if bad == "identity":
                    raw["segment_id"] = "untrusted"
                else:
                    raw["fields"] = []
            else:
                raw = with_rows(payload(ReaderKind.VISION), 1, total=2)
            return ProviderResult(raw, "test")

    provider = Provider()
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(p,),
        readers=(VisionReader(),),
        settings=Settings(provider_max_images=2),
        max_repairs=0,
    )
    assert result.status == "extraction_failed"
    assert provider.calls == 2
    assert result.routes


def test_complementary_reader_pages_merge_before_table_reconciliation(tmp_path):
    from trade_extract.domain.hashing import document_hash
    from trade_extract.domain.readers import TextCharacter

    source, p, payload = fixture(tmp_path)
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
    text_raw = with_rows(payload(ReaderKind.TEXT_LAYER), 1, total=2)
    visual_raw = with_rows(payload(ReaderKind.VISION), 1, page_index=1, offset=1, total=2)
    for raw in (text_raw, visual_raw):
        raw["source"] = source.model_dump()
        for field in raw["fields"].values():
            field["provenance"]["document_hash"] = source.document_hash
    for field in visual_raw["fields"].values():
        field["provenance"]["page_index"] = 1
    chars = list(p.characters)
    for path, field in text_raw["fields"].items():
        if path.startswith("line_items["):
            box = BBox.model_validate(field["provenance"]["bbox"])
            chars.append(TextCharacter("\n", box))
            chars.extend(TextCharacter(c, box) for c in field["value"])
    box = BBox(x0=0.1, y0=0, x1=0.9, y1=0.01)
    pages = tuple(
        replace(
            p,
            page_index=i,
            image=replace(p.image, page_index=i),
            characters=tuple(chars) if i == 0 else (),
            features=PageFeatures(good_text=i == 0, dense_table=i != 0),
            ocr=OCREvidence(
                i, "ok", tables=(TableRegion("r", box, (TableRow(box),), "line_items"),)
            ),
        )
        for i in range(2)
    )
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=pages,
        readers=(
            StaticReader(ReaderKind.TEXT_LAYER, text_raw),
            StaticReader(ReaderKind.VISION, visual_raw),
        ),
        max_repairs=0,
        settings=Settings(routing_min_text_characters=1),
    )
    assert result.status == "extracted", result.errors
    assert result.document.tables["line_items"].row_count_extracted == 2
    assert result.document.tables["line_items"].unresolved_rows == []


def test_explicit_unresolved_rows_remain_visible_and_bad_cache_fails(tmp_path):
    source, p, payload = fixture(tmp_path)
    box = BBox(x0=0.1, y0=0, x1=0.9, y1=0.01)
    p = replace(
        p, ocr=OCREvidence(0, "ok", tables=(TableRegion("r", box, (TableRow(box),), "line_items"),))
    )
    raw = payload(ReaderKind.VISION)
    raw["tables"] = {
        "line_items": {"row_count_stated": 1, "row_count_extracted": 0, "unresolved_rows": [0]}
    }
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(p,),
        readers=(StaticReader(ReaderKind.VISION, raw),),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors
    assert result.document.tables["line_items"].unresolved_rows == [0]
    bad = replace(p, ocr=replace(p.ocr, page_index=9))
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(bad,),
        readers=(),
        max_repairs=0,
    )
    assert result.errors == ("ocr_cached_evidence_invalid",)


def test_native_pixels_reach_provider_with_original_dimensions(tmp_path):
    import io
    import json

    from PIL import Image, ImageDraw

    from trade_extract.services.readers import VisionReader

    source, p, payload = fixture(tmp_path)
    w, h = p.image.width_px, p.image.height_px
    im = Image.new("RGB", (w, h), "white")
    rect = (w // 4, h // 4, 3 * w // 4, h // 2)
    ImageDraw.Draw(im).rectangle((rect[0], rect[1], rect[2] - 1, rect[3] - 1), fill=(20, 70, 220))
    out = io.BytesIO()
    im.save(out, format="PNG")
    box = BBox(x0=rect[0] / w, y0=rect[1] / h, x1=rect[2] / w, y1=rect[3] / h)
    p = replace(
        p,
        image=replace(p.image, image_bytes=out.getvalue(), content_type="image/png"),
        characters=(),
        features=PageFeatures(dense_table=True),
        ocr=OCREvidence(0, "ok", tables=(TableRegion("r", box, (TableRow(box),), "line_items"),)),
    )

    class Provider:
        def vision_extract(self, request):
            meta = json.loads(request.evidence.split("Attached images: ")[1])
            assert meta[1]["crop_rectangle"] == list(rect)
            with Image.open(io.BytesIO(request.pages[1].image_bytes)) as crop:
                assert crop.size == (rect[2] - rect[0], rect[3] - rect[1])
                assert crop.getpixel((crop.width // 2, crop.height // 2)) == pytest.approx(
                    (20, 70, 220), abs=5
                )
            raw = payload(ReaderKind.VISION)
            raw["tables"] = {
                "line_items": {
                    "row_count_stated": 1,
                    "row_count_extracted": 0,
                    "unresolved_rows": [0],
                }
            }
            return ProviderResult(raw, "pixels")

    result = extract(
        Provider(),
        document_type="commercial_invoice",
        source=source,
        pages=(p,),
        readers=(VisionReader(),),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors


@pytest.mark.parametrize("repeat", [False, True])
def test_multipage_vision_validates_partial_scalars_then_complete_document(tmp_path, repeat):
    from trade_extract.domain.hashing import document_hash
    from trade_extract.services.readers import VisionReader

    source, p, payload = fixture(tmp_path)
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
    pages = tuple(
        replace(p, page_index=i, image=replace(p.image, page_index=i), characters=())
        for i in range(2)
    )

    class Provider:
        def vision_extract(self, request):
            i = request.pages[0].page_index
            raw = payload(ReaderKind.VISION)
            raw["source"] = source.model_dump()
            if not repeat:
                raw["fields"] = {
                    k: f for n, (k, f) in enumerate(raw["fields"].items()) if n % 2 == i
                }
            for f in raw["fields"].values():
                f["provenance"].update(page_index=i, document_hash=source.document_hash)
            return ProviderResult(raw, "multipage")

    result = extract(
        Provider(),
        document_type="commercial_invoice",
        source=source,
        pages=pages,
        readers=(VisionReader(),),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors
    assert len(result.document.fields) == len(payload(ReaderKind.VISION)["fields"])


def test_late_named_table_uses_native_second_pass_and_effective_ocr_limits(tmp_path):
    import json

    from trade_extract.services.readers import TextLayerReader, VisionReader

    source, p, payload = fixture(tmp_path)
    w, h = p.image.width_px, p.image.height_px
    raw_structure = [
        {
            "type": "table",
            "bbox": [0.1 * w, 0, 0.9 * w, 0.01 * h],
            "table_name": "line_items",
            "res": {"html": "<tr><td>x</td></tr>", "cell_bbox": [[0.1 * w, 0, 0.9 * w, 0.01 * h]]},
        }
    ]

    class Provider:
        native_calls = 0

        def text_reason(self, request):
            raw = payload(ReaderKind.TEXT_LAYER)
            raw["tables"] = {
                "line_items": {
                    "row_count_stated": 1,
                    "row_count_extracted": 0,
                    "unresolved_rows": [0],
                }
            }
            return ProviderResult(raw, "text")

        def vision_extract(self, request):
            metadata = json.loads(request.evidence.split("Attached images: ")[1])
            assert len(metadata) == 2
            self.native_calls += 1
            raw = with_rows(payload(ReaderKind.VISION), 1)
            for path, f in raw["fields"].items():
                if path.startswith("line_items["):
                    f["provenance"]["bbox"] = dict(x0=0.2, x1=0.8, y0=0.2, y1=0.8)
            return ProviderResult(raw, "native")

    provider = Provider()
    p = replace(p, features=PageFeatures(good_text=True, dense_table=False))
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(p,),
        readers=(
            TextLayerReader(),
            PaddleReader(backend=lambda _: raw_structure),
            VisionReader(),
        ),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors
    assert provider.native_calls == 1


def test_extraction_settings_bound_an_explicit_paddle_reader(tmp_path):
    source, p, _ = fixture(tmp_path)
    w, h = p.image.width_px, p.image.height_px
    raw = [{"type": "text", "res": [{"text": "x", "text_region": [0, 0, w, h]}] * 2}]
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(replace(p, characters=()),),
        readers=(PaddleReader(backend=lambda _: raw),),
        settings=Settings(ocr_max_spans=1),
        max_repairs=0,
    )
    assert result.status == "extraction_failed"
    assert "page:0:ocr_span_budget_exhausted" in result.diagnostics
    assert dict(result.routes[0].availability)["paddle"] is False


def test_late_inspection_failure_retains_its_reason(tmp_path):

    source, p, payload = fixture(tmp_path)
    raw = payload(ReaderKind.TEXT_LAYER)
    raw["tables"] = {
        "line_items": {"row_count_stated": 1, "row_count_extracted": 0, "unresolved_rows": [0]}
    }

    def fail(_):
        raise RuntimeError("private document text")

    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(replace(p, features=PageFeatures(good_text=True, dense_table=False)),),
        readers=(StaticReader(ReaderKind.TEXT_LAYER, raw), PaddleReader(backend=fail)),
        max_repairs=0,
    )
    assert result.status == "extraction_failed"
    assert "page:0:ocr_initialization_or_inference_failed" in result.diagnostics
    assert "private document text" not in str(result.errors)


def test_mixed_routes_and_repairs_never_receive_refused_or_ineligible_pages(tmp_path):
    from trade_extract.domain.hashing import document_hash

    source, p, payload = fixture(tmp_path)
    source = source.model_copy(
        update={
            "page_end": 2,
            "document_hash": document_hash(
                source_file_sha256="a" * 64,
                page_start=0,
                page_end=2,
                document_type="commercial_invoice",
            ),
        }
    )
    pages = (
        replace(p, features=PageFeatures(good_text=True, dense_table=False)),
        replace(p, page_index=1, image=replace(p.image, page_index=1), characters=()),
        replace(
            p,
            page_index=2,
            image=replace(p.image, page_index=2),
            unreadable=True,
            unreadable_reasons=("blurred",),
        ),
    )

    class Reader:
        def __init__(self, name, index):
            self.name, self.index, self.requests = name, index, []

        def read(self, provider, request, pages):
            self.requests.append(request)
            assert tuple(p.page_index for p in pages) == (self.index,)
            if len(self.requests) == 1:
                return ProviderResult({}, "repair")
            raw = payload(self.name)
            raw["source"] = source.model_dump()
            for f in raw["fields"].values():
                f["provenance"].update(page_index=self.index, document_hash=source.document_hash)
            return ProviderResult(raw, "repaired")

    text, vision = Reader(ReaderKind.TEXT_LAYER, 0), Reader(ReaderKind.VISION, 1)
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=pages,
        readers=(text, vision),
        settings=Settings(routing_min_text_characters=1),
    )
    assert result.status == "unreadable" and result.errors == ("page:2:blurred",)
    assert len(text.requests) == len(vision.requests) == 2
    assert text.requests[1].repair_errors and vision.requests[1].repair_errors
    assert next(r for r in result.routes if r.page_index == 2).readers == ()


def test_non_table_digital_companion_does_not_require_ocr_structure(tmp_path):
    from trade_extract.domain.hashing import document_hash

    source, p, payload = fixture(tmp_path)
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
    text = payload(ReaderKind.TEXT_LAYER)
    visual = with_rows(payload(ReaderKind.VISION), 1, page_index=1)
    for raw in (text, visual):
        raw["source"] = source.model_dump()
        for field in raw["fields"].values():
            field["provenance"]["document_hash"] = source.document_hash
    for field in visual["fields"].values():
        field["provenance"]["page_index"] = 1
    box = BBox(x0=0.1, y0=0, x1=0.9, y1=0.01)
    digital = replace(p, features=PageFeatures(good_text=True, dense_table=False))
    scan = replace(
        p,
        page_index=1,
        image=replace(p.image, page_index=1),
        characters=(),
        features=PageFeatures(dense_table=True),
        ocr=OCREvidence(1, "ok", tables=(TableRegion("r", box, (TableRow(box),), "line_items"),)),
    )
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(digital, scan),
        readers=(
            StaticReader(ReaderKind.TEXT_LAYER, text),
            StaticReader(ReaderKind.VISION, visual),
        ),
        settings=Settings(routing_min_text_characters=1),
        max_repairs=0,
    )
    assert result.status == "extracted", result.errors
    assert result.inventory.diagnostics == ()


def test_failed_structure_cannot_disappear_when_provider_omits_table(tmp_path):
    source, p, payload = fixture(tmp_path)
    p = replace(
        p,
        ocr=OCREvidence(0, "ok", diagnostics=("ambiguous_merged_cells",), structure_status="error"),
    )
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(p,),
        readers=(StaticReader(ReaderKind.VISION, payload(ReaderKind.VISION)),),
        max_repairs=0,
    )
    assert result.status == "extraction_failed"
    assert "page:0:independent_structure_unavailable" in result.errors


def test_reader_count_disagreement_is_not_silently_resolved(tmp_path):
    source, p, payload = fixture(tmp_path)
    a, b = payload(ReaderKind.VISION), payload(ReaderKind.VISION)
    for raw, count in ((a, 1), (b, 2)):
        raw["tables"] = {
            "line_items": {
                "row_count_stated": count,
                "row_count_extracted": 0,
                "unresolved_rows": list(range(count)),
            }
        }
    result = extract(
        MockProvider({}),
        document_type="commercial_invoice",
        source=source,
        pages=(p,),
        readers=(StaticReader(ReaderKind.VISION, a), StaticReader(ReaderKind.VISION, b)),
        max_repairs=0,
    )
    assert result.status == "extraction_failed" and result.errors == (
        "table_metadata_disagreement",
    )
