"""Routing, structural parsing and independent crop geometry regression matrix."""

import io
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from trade_extract.config import Settings
from trade_extract.domain.conditioning import VERSION, CoordinateTransform
from trade_extract.domain.inference import PageImage
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import (
    OCREvidence,
    PageFeatures,
    PageInput,
    TableRegion,
    TableRow,
    TextCharacter,
)
from trade_extract.services.readers import paddle as paddle_module
from trade_extract.services.readers.paddle import PaddleReader
from trade_extract.services.routing import route_page
from trade_extract.services.tables import build_inventory, native_crops


def page():
    out = io.BytesIO()
    Image.new("RGB", (1000, 1000), "white").save(out, format="PNG")
    return PageInput(
        0,
        PageImage(0, out.getvalue(), 1000, 1000),
        conditioning_version=VERSION,
        conditioning_status="conditioned",
        transform=CoordinateTransform((1000, 1000), (1000, 1000)),
    )


def test_policy_changes_are_effective_and_recorded():
    p = replace(
        page(),
        characters=(TextCharacter("digital text", BBox(x0=0, y0=0, x1=1, y1=1)),),
        features=PageFeatures(good_text=True, dense_table=False),
    )
    readers = set(ReaderKind)
    a = route_page(p, Settings(routing_min_text_characters=1), readers)
    b = route_page(p, Settings(routing_min_text_characters=100), readers)
    assert a.readers == (ReaderKind.TEXT_LAYER,)
    assert ReaderKind.VISION in b.readers
    assert a.policy != b.policy
    unknown = route_page(page(), Settings(), readers)
    assert unknown.primary == ReaderKind.VISION
    refused = route_page(
        replace(p, unreadable=True, unreadable_reasons=("blurred",)), Settings(), readers
    )
    assert not refused.readers and refused.reasons == ("blurred",)


def test_headers_not_data_and_bad_table_preserves_scalar_spans():
    raw = [
        {
            "type": "text",
            "res": [{"text": "ABC", "text_region": [[10, 10], [100, 10], [100, 30], [10, 30]]}],
        },
        {
            "type": "table",
            "bbox": [0, 100, 1000, 500],
            "table_name": "line_items",
            "res": {
                "html": "<table><thead><tr><th>A</th></tr></thead><tr><td>B</td></tr></table>",
                "cell_bbox": [[0, 100, 1000, 200], [0, 200, 1000, 300]],
            },
        },
    ]
    evidence = PaddleReader(backend=lambda _: raw).inspect(page())
    assert evidence.status == "ok" and evidence.tables[0].rows[0].header
    inv = build_inventory((replace(page(), ocr=evidence),), {0}, Settings())
    assert len(inv.rows) == 1
    raw[1]["res"]["html"] = '<table><tr><td rowspan="2">bad</td></tr></table>'
    bad = PaddleReader(backend=lambda _: raw).inspect(page())
    assert bad.spans == evidence.spans and bad.structure_status == "error"


def test_nan_polygon_and_cached_wrong_identity_are_refused():
    raw = [
        {
            "type": "text",
            "res": [{"text": "ABC", "text_region": [[10, 10], [float("nan"), 20], [30, 30]]}],
        }
    ]
    assert PaddleReader(backend=lambda _: raw).inspect(page()).status == "error"
    cached = replace(page(), ocr=OCREvidence(17, "ok"))
    assert "ocr_identity_version_mismatch" in PaddleReader().inspect(cached).diagnostics


def test_native_crop_landmark_inverts_perspective_independently():
    # x'=x/(1+.0005x), y'=y/(1+.0005x). Independently chosen source landmark (500,400)
    # therefore maps to conditioned (400,320), then local (200,220) in this crop.
    p = replace(
        page(),
        transform=CoordinateTransform(
            (1000, 1000),
            (1000, 1000),
            (1, 0, 0, 0, 1, 0, 0.0005, 0, 1),
            (1, 0, 0, 0, 1, 0, -0.0005, 0, 1),
        ),
    )
    box = BBox(x0=0.2, y0=0.1, x1=0.6, y1=0.5)
    p = replace(
        p, ocr=OCREvidence(0, "ok", tables=(TableRegion("r", box, (TableRow(box),), "line_items"),))
    )
    crop = native_crops((p,), build_inventory((p,), {0}, Settings()), Settings())[0]
    b = crop.page_box(BBox(x0=0.5, y0=0.55, x1=0.51, y1=0.56))
    assert (b.x0 * 1000, b.y0 * 1000) == pytest.approx((400, 320))
    assert p.transform.point(b.x0 * 1000, b.y0 * 1000, inverse=True) == pytest.approx(
        (500, 400), abs=2
    )
    with pytest.raises(ValueError, match="crop_pixel_budget"):
        native_crops(
            (p,),
            build_inventory((p,), {0}, Settings()),
            Settings(table_max_crops=1, table_max_crop_pixels=1),
        )


@pytest.mark.parametrize("feature", ["photograph", "skew", "stamps", "handwriting"])
def test_marked_digital_page_requires_vision(feature):
    p = replace(
        page(),
        characters=(TextCharacter("text", BBox(x0=0, y0=0, x1=1, y1=1)),),
        features=PageFeatures(good_text=True, dense_table=False, **{feature: True}),
    )
    route = route_page(p, Settings(routing_min_text_characters=1), set(ReaderKind))
    assert route.primary == ReaderKind.VISION and ReaderKind.VISION in route.readers


def test_flat_scan_compares_and_unknown_never_shortcuts():
    p = replace(
        page(),
        features=PageFeatures(
            flat_scan=True, photograph=False, skew=False, stamps=False, handwriting=False
        ),
    )
    assert route_page(p, Settings(), set(ReaderKind)).readers == (
        ReaderKind.PADDLE,
        ReaderKind.VISION,
    )
    assert "clean_flat_scan_compare" not in route_page(page(), Settings(), set(ReaderKind)).reasons


def test_overlap_and_structure_limits_have_explicit_diagnostics():
    box = BBox(x0=0.1, y0=0.1, x1=0.9, y1=0.2)
    region = TableRegion("r", box, (TableRow(box),), "line_items")
    p = replace(
        page(), ocr=OCREvidence(0, "ok", tables=(region, replace(region, region_id="other")))
    )
    assert "page:0:overlapping_table_regions" in build_inventory((p,), {0}, Settings()).diagnostics
    assert (
        "table_row_budget_exhausted"
        in build_inventory((p,), {0}, Settings(ocr_max_rows=1)).diagnostics
    )
    with pytest.raises(ValueError, match="total_crop_pixel_budget"):
        native_crops(
            (p,), build_inventory((p,), {0}, Settings()), Settings(table_max_total_crop_pixels=1)
        )


def test_corrupt_and_refused_crop_are_named_failures():
    box = BBox(x0=0.1, y0=0.1, x1=0.9, y1=0.2)
    p = replace(
        page(),
        ocr=OCREvidence(0, "ok", tables=(TableRegion("r", box, (TableRow(box),), "line_items"),)),
    )
    inventory = build_inventory((p,), {0}, Settings())
    with pytest.raises(ValueError, match="table_invalid_image"):
        native_crops(
            (replace(p, image=replace(p.image, image_bytes=b"bad")),), inventory, Settings()
        )
    with pytest.raises(ValueError, match="native_image_unavailable"):
        native_crops((replace(p, unreadable=True),), inventory, Settings())


def test_ocr_failures_hide_exception_content_and_enforce_analysis_budgets():
    def fail(_):
        raise ValueError("private text and filename")

    assert PaddleReader(backend=fail).inspect(page()).diagnostics == (
        "ocr_initialization_or_inference_failed",
    )
    assert PaddleReader(Settings(ocr_max_pixels=1), fail).inspect(page()).diagnostics == (
        "ocr_pixel_budget_exhausted",
    )
    raw = [
        {
            "type": "table",
            "bbox": [0, 0, 1000, 1000],
            "res": {"html": "<tr><td>A</td></tr>", "cell_bbox": [[0, 0, 100, 100]]},
        }
    ]
    e = PaddleReader(Settings(ocr_max_html_bytes=1), lambda _: raw).inspect(page())
    assert e.structure_status == "error" and "ocr_html_budget_exhausted" in e.diagnostics
    raw[0]["res"]["cell_bbox"] *= 2
    e = PaddleReader(Settings(ocr_max_cells=1), lambda _: raw).inspect(page())
    assert "ocr_cell_budget_exhausted" in e.diagnostics


def test_runtime_stages_mobile_ocr_and_lazy_table_recognition(monkeypatch):
    options = {"ocr_calls": 0, "table_calls": 0}

    class OCRBackend:
        def __init__(self, **kwargs):
            options["ocr"] = kwargs

        def predict(self, _):
            options["ocr_calls"] += 1
            raw = SimpleNamespace(
                json={
                    "res": {
                        "rec_texts": ["Widget"],
                        "rec_polys": [[[10, 10], [100, 10], [100, 30], [10, 30]]],
                        "rec_scores": [0.9],
                    }
                }
            )
            options["raw"] = raw
            yield raw

    class TableBackend:
        def __init__(self, **kwargs):
            options["table"] = kwargs

        def predict(self, _, **kwargs):
            options["table_calls"] += 1
            options["predict"] = kwargs
            yield {
                "res": {
                    "table_res_list": [
                        {
                            "pred_html": "<table><tr><td>Widget</td></tr></table>",
                            "cell_box_list": [[10, 10, 100, 30]],
                        }
                    ]
                }
            }

    monkeypatch.setattr(paddle_module, "_OCR_ENGINE", None)
    monkeypatch.setattr(paddle_module, "_TABLE_ENGINE", None)
    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=OCRBackend, TableRecognitionPipelineV2=TableBackend),
    )

    settings = Settings(
        ocr_detection_model="custom-mobile-det",
        ocr_recognition_model="custom-mobile-rec",
        table_wired_structure_model="custom-wired",
        table_wireless_structure_model="custom-wireless",
    )
    reader = PaddleReader(settings)
    evidence = reader.inspect(page())
    assert evidence.status == "ok" and evidence.structure_status == "unavailable"
    assert options["ocr_calls"] == 1 and options["table_calls"] == 0

    structured = reader.inspect(replace(page(), ocr=evidence), require_structure=True)

    assert structured.structure_status == "ok"
    assert options["ocr_calls"] == 1 and options["table_calls"] == 1
    assert options["ocr"]["text_detection_model_name"] == "custom-mobile-det"
    assert options["ocr"]["text_recognition_model_name"] == "custom-mobile-rec"
    assert options["table"] == {
        "device": "cpu",
        "wired_table_structure_recognition_model_name": "custom-wired",
        "wireless_table_structure_recognition_model_name": "custom-wireless",
        "text_detection_model_name": "custom-mobile-det",
        "text_recognition_model_name": "custom-mobile-rec",
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_layout_detection": False,
        "use_ocr_model": False,
    }
    assert options["predict"]["use_layout_detection"] is False
    assert options["predict"]["use_ocr_model"] is False
    assert options["predict"]["overall_ocr_res"] is options["raw"]
    assert structured.spans[0].text == "Widget"
    assert structured.spans[0].bbox == BBox(x0=0.01, y0=0.01, x1=0.1, y1=0.03)
    assert structured.structure_status == "ok"
    assert structured.tables[0].rows[0].bbox == structured.tables[0].rows[0].cells[0]

    # Normalized evidence alone is not a valid PaddleX handoff. A new reader
    # conservatively repeats mobile OCR instead of fabricating OCRResult internals.
    external = PaddleReader(settings).inspect(replace(page(), ocr=evidence), require_structure=True)
    assert external.structure_status == "ok"
    assert options["ocr_calls"] == 2


def test_native_table_failure_and_overflow_preserve_base_spans(monkeypatch):
    raw = SimpleNamespace(
        json={
            "res": {
                "rec_texts": ["kept"],
                "rec_polys": [[[10, 10], [50, 10], [50, 30], [10, 30]]],
                "rec_scores": [0.8],
            }
        }
    )

    class OCRBackend:
        def __init__(self, **_):
            pass

        def predict(self, _):
            yield raw

    class FailingTable:
        def __init__(self, **_):
            pass

        def predict(self, *_args, **_kwargs):
            raise RuntimeError("private failure")

    monkeypatch.setattr(paddle_module, "_OCR_ENGINE", None)
    monkeypatch.setattr(paddle_module, "_TABLE_ENGINE", None)
    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=OCRBackend, TableRecognitionPipelineV2=FailingTable),
    )
    failed = PaddleReader().inspect(page(), require_structure=True)
    assert failed.status == "ok" and failed.structure_status == "error"
    assert failed.spans[0].text == "kept"
    assert failed.diagnostics == ("ocr_initialization_or_inference_failed",)

    class OverflowTable(FailingTable):
        def predict(self, *_args, **_kwargs):
            for _ in range(3):
                yield {"res": {"table_res_list": []}}

    monkeypatch.setattr(paddle_module, "_TABLE_ENGINE", None)
    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=OCRBackend, TableRecognitionPipelineV2=OverflowTable),
    )
    overflow = PaddleReader(Settings(ocr_max_regions=2)).inspect(page(), require_structure=True)
    assert overflow.status == "ok" and overflow.structure_status == "error"
    assert overflow.spans[0].text == "kept"
    assert overflow.diagnostics == ("ocr_region_budget_exhausted",)


def test_html_row_headers_and_slight_cell_alignment_are_supported():
    raw = [
        {
            "type": "table",
            "bbox": [0, 0, 1000, 1000],
            "table_name": "line_items",
            "res": {
                "html": '<table><tr><th scope="col">A</th><th>B</th></tr>'
                '<tr><th scope="row">one</th><td>two</td></tr></table>',
                "cell_bbox": [
                    [0, 0, 400, 90],
                    [400, 1, 900, 91],
                    [0, 100, 400, 190],
                    [400, 101, 900, 191],
                ],
            },
        }
    ]
    e = PaddleReader(backend=lambda _: raw).inspect(page())
    assert e.structure_status == "ok", e.diagnostics
    assert [r.header for r in e.tables[0].rows] == [True, False]
    raw[0]["res"]["cell_bbox"] = [
        [0, 0, 300, 90],
        [300, 0, 600, 90],
        [600, 0, 900, 90],
        [0, 100, 900, 190],
    ]
    bad = PaddleReader(backend=lambda _: raw).inspect(page())
    assert "ambiguous_table_cell_correspondence" in bad.diagnostics


def test_v3_invalid_table_retains_valid_scalar_text():
    class Backend:
        def predict(self, _):
            yield {
                "res": {
                    "overall_ocr_res": {
                        "rec_texts": ["ABC"],
                        "rec_polys": [[[10, 10], [100, 10], [100, 30], [10, 30]]],
                    },
                    "table_res_list": [{"pred_html": "<tr><td>x</td></tr>", "cell_box_list": []}],
                }
            }

    e = PaddleReader(backend=Backend()).inspect(page())
    assert e.spans[0].text == "ABC" and e.structure_status == "error"
