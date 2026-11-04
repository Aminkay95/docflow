"""Lazy process-serialized OCR and bounded, validated structural evidence."""

from __future__ import annotations

import io
import json
import math
import threading
from dataclasses import replace
from html.parser import HTMLParser
from typing import Any

from trade_extract.config import Settings
from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import OCREvidence, OCRSpan, PageInput, TableRegion, TableRow
from trade_extract.providers.base import ModelProvider

_LOCK = threading.Lock()
_ENGINE: Any = None
# PPStructureV3 eagerly constructs ChartRecognition even when it is disabled. Point that
# unreachable slot at an already-required lightweight classifier until Paddle honors the flag.
_DISABLED_CHART_PLACEHOLDER_MODEL = "PP-LCNet_x1_0_table_cls"


class _Rows(HTMLParser):
    def __init__(self, maximum: int):
        super().__init__()
        self.rows: list[bool] = []
        self.header = False
        self.in_head = False
        self.maximum = maximum
        self.cells: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "thead":
            self.in_head = True
        if tag == "tr":
            if len(self.rows) >= self.maximum:
                raise ValueError("ocr_row_budget_exhausted")
            self.rows.append(self.in_head)
            self.cells.append(0)
        if tag in ("td", "th"):
            if not self.cells:
                raise ValueError("ocr_cell_without_row")
            self.cells[-1] += 1
        if tag == "th" and self.rows and dict(attrs).get("scope") != "row":
            self.rows[-1] = True
        for key, value in attrs:
            if key in ("rowspan", "colspan") and value != "1":
                raise ValueError("ambiguous_merged_cells")

    def handle_endtag(self, tag: str) -> None:
        if tag == "thead":
            self.in_head = False


def _box(value: Any, width: int, height: int) -> BBox:
    if not isinstance(value, (list, tuple)):
        raise ValueError("ocr_invalid_geometry")
    if len(value) == 4 and all(isinstance(v, (float, int)) for v in value):
        points = [(value[0], value[1]), (value[2], value[3])]
    else:
        points = list(value)
    if not points or any(
        not isinstance(p, (list, tuple))
        or len(p) != 2
        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in p)
        for p in points
    ):
        raise ValueError("ocr_invalid_geometry")
    if any(not 0 <= x <= width or not 0 <= y <= height for x, y in points):
        raise ValueError("ocr_outside_page")
    return BBox(
        x0=min(p[0] for p in points) / width,
        y0=min(p[1] for p in points) / height,
        x1=max(p[0] for p in points) / width,
        y1=max(p[1] for p in points) / height,
    )


def validate_evidence(e: OCREvidence, page: PageInput, settings: Settings) -> OCREvidence:
    if e.page_index != page.page_index or e.version != "paddle-structure-v1":
        raise ValueError("ocr_identity_version_mismatch")
    if e.status not in ("ok", "error", "unavailable") or e.structure_status not in (
        "ok",
        "error",
        "unavailable",
    ):
        raise ValueError("ocr_invalid_status")
    if len(e.spans) > settings.ocr_max_spans or len(e.tables) > settings.ocr_max_regions:
        raise ValueError("ocr_evidence_budget_exhausted")
    if (
        sum(len(t.rows) for t in e.tables) > settings.ocr_max_rows
        or sum(len(r.cells) for t in e.tables for r in t.rows) > settings.ocr_max_cells
    ):
        raise ValueError("ocr_structure_budget_exhausted")
    for box in [
        *(s.bbox for s in e.spans),
        *(t.bbox for t in e.tables),
        *(r.bbox for t in e.tables for r in t.rows),
        *(c for t in e.tables for r in t.rows for c in r.cells),
    ]:
        BBox.model_validate(box.model_dump())
        if not box.width or not box.height:
            raise ValueError("ocr_empty_geometry")
    if any(
        not isinstance(s.text, str)
        or (
            s.confidence is not None
            and (not math.isfinite(s.confidence) or not 0 <= s.confidence <= 1)
        )
        for s in e.spans
    ):
        raise ValueError("ocr_invalid_span")
    for t in e.tables:
        for row in t.rows:
            if not contains(t.bbox, row.bbox) or any(not contains(row.bbox, c) for c in row.cells):
                raise ValueError("ocr_structure_geometry_mismatch")
    if len({t.region_id for t in e.tables}) != len(e.tables):
        raise ValueError("ocr_duplicate_region_identity")
    return e


def contains(a: BBox, b: BBox) -> bool:
    return a.x0 <= b.x0 <= b.x1 <= a.x1 and a.y0 <= b.y0 <= b.y1 <= a.y1


class PaddleReader:
    name = ReaderKind.PADDLE

    def __init__(self, settings: Settings | None = None, backend: Any = None):
        self.settings = settings or Settings()
        self.backend = backend

    def inspect(self, page: PageInput) -> OCREvidence:
        global _ENGINE
        s = self.settings
        spans: list[OCRSpan] = []
        if page.is_unreadable:
            return OCREvidence(
                page.page_index,
                "unavailable",
                diagnostics=("unreadable",),
                structure_status="unavailable",
            )
        try:
            if page.ocr is not None:
                return validate_evidence(page.ocr, page, s)
            if page.image is None or not page.vision_ready:
                return OCREvidence(
                    page.page_index,
                    "unavailable",
                    diagnostics=("ocr_no_conditioned_image",),
                    structure_status="unavailable",
                )
            if page.image.width_px * page.image.height_px > s.ocr_max_pixels:
                raise ValueError("ocr_pixel_budget_exhausted")
            from PIL import Image

            with Image.open(io.BytesIO(page.image.image_bytes)) as im:
                if im.size != (page.image.width_px, page.image.height_px):
                    raise ValueError("ocr_image_dimensions_mismatch")
                with _LOCK:
                    if self.backend is None:
                        from paddleocr import PPStructureV3

                        if _ENGINE is None:
                            _ENGINE = PPStructureV3(
                                device="cpu",
                                chart_recognition_model_name=_DISABLED_CHART_PLACEHOLDER_MODEL,
                                use_doc_orientation_classify=False,
                                use_doc_unwarping=False,
                                use_textline_orientation=False,
                                use_formula_recognition=False,
                                use_seal_recognition=False,
                                use_table_recognition=True,
                                use_chart_recognition=False,
                            )
                        self.backend = _ENGINE
                    import numpy as np

                    array = np.array(im.convert("RGB"))[:, :, ::-1]
                    if hasattr(self.backend, "predict"):
                        results = iter(self.backend.predict(array))
                        first = next(results, None)
                        if first is None or next(results, None) is not None:
                            raise ValueError("ocr_invalid_page_results")
                        raw = normalize_v3(first, s)
                    else:
                        raw = self.backend(array)
            if not isinstance(raw, list) or len(raw) > s.ocr_max_regions:
                raise ValueError("ocr_region_budget_exhausted")
            tables: list[TableRegion] = []
            diagnostics: list[str] = []
            total_cells = 0
            total_rows = 0
            w, h = page.image.width_px, page.image.height_px
            for index, region in enumerate(raw):
                if not isinstance(region, dict):
                    raise ValueError("ocr_malformed_region")
                result = region.get("res", [])
                if isinstance(result, list):
                    if len(spans) + len(result) > s.ocr_max_spans:
                        raise ValueError("ocr_span_budget_exhausted")
                    for item in result:
                        spans.append(
                            OCRSpan(
                                str(item["text"]),
                                _box(item["text_region"], w, h),
                                item.get("confidence"),
                            )
                        )
                if region.get("type") != "table":
                    continue
                try:
                    if not isinstance(result, dict):
                        raise ValueError("ocr_missing_table_structure")
                    html = result.get("html", "")
                    cells = result.get("cell_bbox", region.get("cell_bbox", []))
                    if not isinstance(html, str) or len(html.encode()) > s.ocr_max_html_bytes:
                        raise ValueError("ocr_html_budget_exhausted")
                    if not isinstance(cells, list) or total_cells + len(cells) > s.ocr_max_cells:
                        raise ValueError("ocr_cell_budget_exhausted")
                    total_cells += len(cells)
                    parser = _Rows(s.ocr_max_rows - total_rows)
                    parser.feed(html)
                    if sum(parser.cells) != len(cells) or any(n == 0 for n in parser.cells):
                        raise ValueError("ambiguous_table_cell_correspondence")
                    boxes = [_box(c, w, h) for c in cells]
                    grouped: list[list[BBox]] = []
                    for b in boxes:
                        matching = [
                            group
                            for group in grouped
                            if all(
                                min(b.y1, c.y1) - max(b.y0, c.y0) > 0.5 * min(b.height, c.height)
                                for c in group
                            )
                        ]
                        if len(matching) > 1:
                            raise ValueError("ambiguous_table_row_correspondence")
                        if matching:
                            matching[0].append(b)
                        else:
                            grouped.append([b])
                    ordered = sorted(
                        ((min(c.y0 for c in group), max(c.y1 for c in group)), group)
                        for group in grouped
                    )
                    if len(ordered) != len(parser.rows) or not ordered:
                        raise ValueError("ambiguous_table_row_correspondence")
                    if [len(cs) for _, cs in ordered] != parser.cells:
                        raise ValueError("ambiguous_table_cell_correspondence")
                    total_rows += len(ordered)
                    rows = tuple(
                        TableRow(
                            BBox(
                                x0=min(c.x0 for c in cs), y0=y[0], x1=max(c.x1 for c in cs), y1=y[1]
                            ),
                            tuple(cs),
                            header,
                        )
                        for (y, cs), header in zip(ordered, parser.rows, strict=True)
                    )
                    tables.append(
                        TableRegion(
                            str(index), _box(region["bbox"], w, h), rows, region.get("table_name")
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    diagnostics.append(_safe_error(exc))
            return validate_evidence(
                OCREvidence(
                    page.page_index,
                    "ok",
                    tuple(spans),
                    tuple(tables),
                    tuple(diagnostics),
                    structure_status="error" if diagnostics else "ok",
                ),
                page,
                s,
            )
        except ImportError:
            return OCREvidence(
                page.page_index,
                "unavailable",
                tuple(spans),
                diagnostics=("ocr_runtime_unavailable",),
                structure_status="unavailable",
            )
        except Exception as exc:
            code = _safe_error(exc)
            return OCREvidence(
                page.page_index,
                "error",
                tuple(spans),
                diagnostics=(code,),
                structure_status="error",
            )

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None:
        evidence = [
            validate_evidence(e, p, self.settings)
            for p in pages
            if not p.is_unreadable and (e := p.ocr) is not None and e.spans
        ]
        if not evidence:
            return None
        return provider.text_reason(
            replace(
                request,
                pages=(),
                evidence=request.evidence
                + '\nRequired provenance.reader: "paddle"\nOCR spans: '
                + json.dumps(
                    [
                        {
                            "page_index": e.page_index,
                            "spans": [
                                {"text": s.text, "bbox": s.bbox.model_dump()} for s in e.spans
                            ],
                        }
                        for e in evidence
                    ]
                ),
            )
        )


def verify_ocr_box(page: PageInput, text: str, bbox: BBox) -> bool:
    return bool(page.ocr and any(s.text == text and s.bbox == bbox for s in page.ocr.spans))


def normalize_v3(result: Any, settings: Settings) -> list[dict[str, Any]]:
    """Normalize documented PPStructureV3 JSON without materializing image arrays."""
    payload = result.json if hasattr(result, "json") else result
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        raise ValueError("ocr_malformed_result")
    data = payload.get("res", payload)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("overall_ocr_res"), dict)
        or not isinstance(data.get("table_res_list"), list)
    ):
        raise ValueError("ocr_malformed_result")
    ocr = data.get("overall_ocr_res", {})
    texts = ocr.get("rec_texts", [])
    polygons = ocr.get("rec_polys", ocr.get("dt_polys", []))
    scores = ocr.get("rec_scores", [])
    if scores and len(scores) != len(texts):
        raise ValueError("ocr_span_budget_or_shape")
    if len(texts) > settings.ocr_max_spans or len(texts) != len(polygons):
        raise ValueError("ocr_span_budget_or_shape")

    def plain(value: Any) -> Any:
        return value.tolist() if hasattr(value, "tolist") else value

    output: list[dict[str, Any]] = [
        {
            "type": "text",
            "res": [
                {
                    "text": text,
                    "text_region": plain(poly),
                    "confidence": float(scores[i]) if i < len(scores) else None,
                }
                for i, (text, poly) in enumerate(zip(texts, polygons, strict=True))
            ],
        }
    ]
    tables = data.get("table_res_list", [])
    if len(tables) + 1 > settings.ocr_max_regions:
        raise ValueError("ocr_region_budget_exhausted")
    for table in tables:
        if not isinstance(table, dict):
            output.append({"type": "table", "res": {}})
            continue
        cells = table.get("cell_box_list", [])
        # Keep table normalization failures local so validated overall OCR survives.
        if not isinstance(cells, list) or len(cells) > settings.ocr_max_cells:
            output.append({"type": "table", "res": {"html": "", "cell_bbox": cells}})
            continue
        try:
            region_box = table.get("table_region_bbox") or _cell_union(cells)
        except (ValueError, TypeError, KeyError, IndexError):
            region_box = None
        output.append(
            {
                "type": "table",
                "bbox": region_box,
                "table_name": table.get("table_name"),
                "res": {"html": table.get("pred_html", ""), "cell_bbox": [plain(c) for c in cells]},
            }
        )
    return output


def _cell_union(cells: Any) -> list[float]:
    points: list[Any] = []
    for c in cells:
        c = c.tolist() if hasattr(c, "tolist") else c
        if len(c) == 4 and all(isinstance(v, (int, float)) for v in c):
            points.extend(((c[0], c[1]), (c[2], c[3])))
        else:
            points.extend(c)
    if not points:
        raise ValueError("ocr_missing_table_structure")
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def _safe_error(exc: Exception) -> str:
    codes = {
        "ocr_identity_version_mismatch",
        "ocr_invalid_status",
        "ocr_evidence_budget_exhausted",
        "ocr_structure_budget_exhausted",
        "ocr_structure_geometry_mismatch",
        "ocr_duplicate_region_identity",
        "ocr_pixel_budget_exhausted",
        "ocr_image_dimensions_mismatch",
        "ocr_invalid_page_results",
        "ocr_region_budget_exhausted",
        "ocr_span_budget_exhausted",
        "ocr_html_budget_exhausted",
        "ocr_cell_budget_exhausted",
        "ocr_row_budget_exhausted",
        "ocr_invalid_geometry",
        "ocr_outside_page",
        "ocr_empty_geometry",
        "ocr_invalid_span",
        "ocr_malformed_region",
        "ocr_missing_table_structure",
        "ambiguous_table_row_correspondence",
        "ambiguous_table_cell_correspondence",
        "ambiguous_merged_cells",
        "ocr_cell_without_row",
        "ocr_malformed_result",
        "ocr_span_budget_or_shape",
    }
    return (
        str(exc)
        if isinstance(exc, ValueError) and str(exc) in codes
        else "ocr_initialization_or_inference_failed"
    )
