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


def test_runtime_disables_unused_chart_recognition(monkeypatch):
    options = {}

    class Backend:
        def __init__(self, **kwargs):
            options.update(kwargs)

        def predict(self, _):
            yield {
                "res": {
                    "overall_ocr_res": {"rec_texts": [], "rec_polys": []},
                    "table_res_list": [],
                }
            }

    monkeypatch.setattr(paddle_module, "_ENGINE", None)
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PPStructureV3=Backend))

    evidence = PaddleReader().inspect(page())

    assert evidence.status == "ok"
    assert options["chart_recognition_model_name"] == "PP-LCNet_x1_0_table_cls"
    assert options["use_chart_recognition"] is False
    assert options["use_table_recognition"] is True


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
