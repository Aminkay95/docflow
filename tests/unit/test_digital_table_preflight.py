from dataclasses import replace

import pytest

from tests.unit.test_invoice_phases import Scripted, fixture
from tests.unit.test_routing_tables_paddle import page as conditioned_page
from trade_extract.config import Settings
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import OCREvidence, TextCharacter
from trade_extract.services.extraction import extract
from trade_extract.services.readers import TextLayerReader
from trade_extract.services.readers.paddle import PaddleReader


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
