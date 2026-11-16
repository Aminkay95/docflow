from trade_extract.config import Settings
from trade_extract.domain.fields import Field
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import OCREvidence, OCRSpan, PageInput, TableRegion, TableRow
from trade_extract.review.line_items import document_line_items
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS, get_schema
from trade_extract.services.readers.base import INVOICE_TYPES, invoice_phase_schema
from trade_extract.services.tables import build_inventory, mismatched_table_columns


def test_every_schema_can_plan_and_review_its_declared_tables():
    for name, spec in DOCUMENT_SCHEMAS.items():
        header = invoice_phase_schema(name, "header_party")["allowed_scalar_paths"]
        totals = (
            invoice_phase_schema(name, "totals_tax")["allowed_scalar_paths"]
            if name in INVOICE_TYPES
            else []
        )
        assert set(spec.model.field_set.required_scalar_paths) <= set(header + totals)
        fields = {}
        for table in spec.tables:
            paths = invoice_phase_schema(name, table, rows=(0,))["allowed_scalar_paths"]
            assert paths and all(
                path.startswith(f"{table}[0].") and "[]" not in path for path in paths
            )
            fields[paths[0]] = {"value": "source value"}
        fields["extension.note"] = {"value": "standalone"}
        projection = document_line_items(name, fields, {})
        assert set(projection["scalar_fields"]) == {"extension.note"}
        assert set(projection["table_panels"]) == {
            "items" if table == "line_items" else table for table in spec.tables
        }


def test_ocr_header_mapping_rejects_net_weight_as_gross_weight():
    keys = ("Container", "Volume", "Net Weight", "G.Weight")
    header = tuple(BBox(x0=i / 4, x1=(i + 1) / 4, y0=0.1, y1=0.2) for i in range(4))
    body = tuple(BBox(x0=i / 4, x1=(i + 1) / 4, y0=0.2, y1=0.3) for i in range(4))
    region = TableRegion(
        "grid",
        BBox(x0=0, x1=1, y0=0.1, y1=0.3),
        (
            TableRow(BBox(x0=0, x1=1, y0=0.1, y1=0.2), header, header=True),
            TableRow(BBox(x0=0, x1=1, y0=0.2, y1=0.3), body),
        ),
    )
    spans = tuple(OCRSpan(label, cell) for label, cell in zip(keys, header, strict=True))
    page = PageInput(0, ocr=OCREvidence(0, "ok", spans=spans, tables=(region,)))
    inventory = build_inventory((page,), {0}, Settings(), get_schema("delivery_order"))
    assert not inventory.diagnostics
    assert dict(inventory.rows[0].columns) == dict(
        zip(("number", "volume", "net_weight", "gross_weight"), body, strict=True)
    )
    field = Field.model_validate(
        {
            "kind": "scalar",
            "value": "17,000.000",
            "value_type": "decimal",
            "agreement": "single_reader",
            "provenance": {
                "bbox": body[2],
                "page_index": 0,
                "page_number": 1,
                "coordinate_space": "normalized_top_left",
                "reader": "paddle",
                "document_hash": "sha256:" + "a" * 64,
            },
        }
    )
    assert mismatched_table_columns(inventory, {"containers[0].net_weight": field}) == ()
    assert mismatched_table_columns(inventory, {"containers[0].gross_weight": field}) == (
        "containers[0].gross_weight",
    )
