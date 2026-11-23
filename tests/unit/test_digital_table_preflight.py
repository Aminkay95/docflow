from dataclasses import replace
from pathlib import Path

import pytest

from tests.unit.test_invoice_phases import Scripted, fixture
from tests.unit.test_routing_tables_paddle import page as conditioned_page
from trade_extract.config import Settings
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import OCREvidence, PageInput, TextCharacter
from trade_extract.services.extraction import extract
from trade_extract.services.page_model import render_container
from trade_extract.services.readers import TextLayerReader
from trade_extract.services.readers.paddle import PaddleReader
from trade_extract.services.readers.text_layer import verify_text_box


def test_ocean_bill_of_lading_keeps_fields_when_reverse_is_an_image(monkeypatch):
    def unexpected_ocr(*args, **kwargs):
        pytest.fail("verified embedded shipment data should reach review without table OCR")

    monkeypatch.setattr(PaddleReader, "inspect", unexpected_ocr)
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "test_docs/bill_of_lading/ocean-bill-of-lading-adecc018-filled.pdf"
    )
    pages = tuple(
        PageInput(page.page_index, characters=page.characters)
        for page in render_container(
            fixture_path,
            "application/pdf",
            document=fixture_path.name,
            dpi=72,
            thumbnail_max_px=None,
            quality=80,
        )
    )
    source, _, _ = fixture("bill_of_lading")
    source = source.model_copy(
        update={
            "page_end": 1,
            "page_count": 2,
            "document_hash": document_hash(
                source_file_sha256="a" * 64,
                page_start=0,
                page_end=1,
                document_type="bill_of_lading",
            ),
        }
    )
    settings = Settings(provider="local", stage_prompts_enabled=True)
    provider = Scripted({})
    outcome = extract(
        provider, document_type="bill_of_lading", source=source, pages=pages, settings=settings
    )

    assert outcome.document is not None
    assert outcome.document.status == "review_required"
    assert "page:1:image_only_continuation_requires_review" in outcome.errors
    fields = outcome.document.fields
    assert {
        path: fields[path].value
        for path in (
            "bl.number",
            "shipper.name",
            "consignee.name",
            "containers[0].number",
            "containers[0].gross_weight_kg",
            "line_items[0].description",
        )
    } == {
        "bl.number": "PLMAAJEA00081",
        "shipper.name": "4G LOGISTICS INDIA PVT LTD",
        "consignee.name": "AL NASER TRADING COMPANY LLC",
        "containers[0].number": "ABCU9877666",
        "containers[0].gross_weight_kg": "18,000.000",
        "line_items[0].description": "VALVE MATERIALS FOR MACHINERY PARTS",
    }
    assert "containers[0].seal" not in fields
    assert {name: table.row_count_extracted for name, table in outcome.document.tables.items()} == {
        "containers": 1,
        "line_items": 1,
    }
    assert all(
        verify_text_box(pages[0], field.value, field.provenance.bbox) for field in fields.values()
    )
    assert not provider.requests


@pytest.mark.parametrize("source_layout", ["digital", "ambiguous", "scan"])
def test_ocr_runs_only_when_digital_table_preflight_cannot_confirm_layout(
    monkeypatch, source_layout
):
    calls = []

    def inspect(self, page, *, require_structure=False):
        calls.append((page.page_index, require_structure))
        return OCREvidence(0, "unavailable", structure_status="unavailable")

    monkeypatch.setattr(PaddleReader, "inspect", inspect)
    characters = []

    def add_line(parts, y):
        for text, x in parts:
            characters.extend(
                TextCharacter(
                    char,
                    BBox(x0=x + i * 0.005, x1=x + i * 0.005 + 0.004, y0=y, y1=y + 0.008),
                )
                for i, char in enumerate(text)
            )
        characters.append(TextCharacter("\n", BBox(x0=0, x1=0.001, y0=y, y1=y + 0.008)))

    if source_layout != "scan":
        add_line((("DELIVERY ORDER - TEST SHIPMENT", 0.04),), 0.06)
        if source_layout == "digital":
            add_line(
                (("Container ", 0.04), ("Size ", 0.25), ("Volume ", 0.57), ("G.Weight", 0.75)), 0.20
            )
        else:
            add_line((("Shipping reference", 0.04),), 0.20)
        add_line(
            (("ABCU9877666 ", 0.04), ("20' DC ", 0.25), ("24.000 ", 0.57), ("18,000.000", 0.75)),
            0.22,
        )
    kind = "delivery_order"
    source, _, _ = fixture(kind)
    page = replace(conditioned_page(), characters=tuple(characters))
    settings = Settings(routing_min_text_characters=20)
    provider = Scripted({"containers[0].number": ("ABCU9877666", "p0l2")})
    result = extract(
        provider,
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(TextLayerReader(), PaddleReader(settings)),
        settings=settings,
        max_repairs=0,
    )
    if source_layout == "digital":
        assert not calls
        assert result.document is not None
        assert result.document.tables["containers"].row_count_extracted == 1
        assert result.document.fields["containers[0].gross_weight"].value == "18,000.000"
    else:
        assert calls == [(0, True)]


@pytest.mark.parametrize(
    "case", ["table_only", "complete", "extra_field", "mismatched_total", "provider_priority"]
)
def test_item_rate_invoice_grid_is_read_without_ocr(monkeypatch, case):
    from trade_extract.schemas.registry import get_schema
    from trade_extract.services.table_layout import digital_grids

    characters = []

    def add_line(parts, y):
        for text, x in parts:
            characters.extend(
                TextCharacter(
                    char, BBox(x0=x + i * 0.004, x1=x + i * 0.004 + 0.003, y0=y, y1=y + 0.008)
                )
                for i, char in enumerate(text)
            )
        characters.append(TextCharacter("\n", BBox(x0=0, x1=0.001, y0=y, y1=y + 0.008)))

    add_line((("COMMERCIAL INVOICE", 0.04),), 0.03)
    if case != "table_only":
        for i, text in enumerate(
            (
                "Invoice Number: INV123",
                "Issue Date: 2026-11-10",
                "Seller: Shipment Services Ltd",
                "Buyer: Example Logistics Ltd",
                "Currency: USD",
            )
        ):
            add_line(((text, 0.04),), 0.06 + i * 0.02)
    add_line((("Item ", 0.04), ("Quantity ", 0.60), ("Rate ", 0.76), ("Amount", 0.87)), 0.20)
    price = "$500.00" if case == "table_only" else "500.00"
    add_line(
        (("Example shipment service ", 0.04), ("1 ", 0.60), (f"{price} ", 0.74), (price, 0.87)),
        0.22,
    )
    if case != "table_only":
        due = "505.00" if case == "mismatched_total" else "500.00"
        for i, text in enumerate(
            (
                "Subtotal: USD 500.00",
                "Total Excluding Tax: USD 500.00",
                "Tax Amount: USD 0.00",
                "Total Including Tax: USD 500.00",
                f"Total Amount Due: USD {due}",
            )
        ):
            add_line(((text, 0.40),), 0.40 + i * 0.025)
    if case == "extra_field":
        add_line((("Payment terms: Within 30 days", 0.04),), 0.70)
    page = replace(conditioned_page(), characters=tuple(characters))
    settings = Settings(
        provider="mock",
        routing_min_text_characters=20,
        llm_extraction_priority=case == "provider_priority",
    )
    grids = digital_grids(page, get_schema("commercial_invoice"), 100)
    assert len(grids) == 1
    assert grids[0].columns == ("description", "quantity", "unit_price", "line_extension_amount")
    assert len(grids[0].rows) == 1

    def unexpected_ocr(*args, **kwargs):
        pytest.fail("verified digital invoice grid must avoid OCR")

    monkeypatch.setattr(PaddleReader, "inspect", unexpected_ocr)
    source, _, _ = fixture("commercial_invoice")
    provider = Scripted({})
    result = extract(
        provider,
        document_type="commercial_invoice",
        source=source,
        pages=(page,),
        readers=(TextLayerReader(), PaddleReader(settings)),
        settings=settings,
        max_repairs=0,
    )
    assert result.document is not None
    assert result.document.fields["line_items[0].description"].value == "Example shipment service"
    assert result.document.fields["line_items[0].line_extension_amount"].value == "500.00"
    if case == "complete":
        assert not provider.requests
        assert "provider_skipped_local_complete_invoice" in result.diagnostics
        assert result.document.fields["totals.tax_amount"].value == "0.00"
        assert result.document.tables["line_items"].row_count_extracted == 1
    else:
        assert provider.requests
        assert "provider_skipped_local_complete_invoice" not in result.diagnostics
