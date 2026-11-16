from dataclasses import replace

import pytest

from tests.unit.test_invoice_phases import Scripted, fixture
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import OCREvidence, TableRegion, TableRow, TextCharacter
from trade_extract.services.extraction import extract
from trade_extract.services.readers import TextLayerReader


@pytest.mark.parametrize("printed_number", [False, True])
def test_delivery_order_keeps_containers_and_rejects_an_hbl_as_the_order_number(printed_number):
    kind = "delivery_order"
    source, page, _ = fixture(kind)
    chars = []

    def add_line(text, x, y):
        chars.extend(
            TextCharacter(
                char, BBox(x0=x + i * 0.005, x1=x + i * 0.005 + 0.004, y0=y, y1=y + 0.008)
            )
            for i, char in enumerate(text)
        )
        chars.append(TextCharacter("\n", BBox(x0=0, x1=0.001, y0=y, y1=y + 0.008)))

    # The PDF stores the two column labels in a different source run from the HBL value.
    add_line("HBL-001", 0.18, 0.10)
    add_line("HBL No.", 0.04, 0.10)
    add_line("D/O No. :" + (" DO-1001" if printed_number else ""), 0.50, 0.10)
    add_line("MBL No. MBL-001", 0.04, 0.13)
    add_line("Date : 17-MAY-23", 0.75, 0.07)
    add_line("Consignee : AL NASER TRADING COMPANY LLC Place of Delivery : JEBEL ALI", 0.04, 0.16)
    add_line("ABCU9877666 SL345566 125 PACKAGES", 0.04, 0.32)
    add_line("Volume            G.Weight", 0.73, 0.30)
    add_line("24.000", 0.73, 0.32)
    add_line("18,000.000", 0.818, 0.32)
    row = TableRow(BBox(x0=0, x1=1, y0=0.31, y1=0.34))
    region = TableRegion("cargo", row.bbox, (row,), "containers")
    page = replace(page, characters=tuple(chars), ocr=OCREvidence(0, "ok", tables=(region,)))
    provider = Scripted(
        {
            "delivery_order.number": ("DO-1001", "p0l2") if printed_number else ("HBL-001", "p0l0"),
            "delivery_order.issue_date": ("17-MAY-23", "p0l4"),
            "bill_of_lading.number": ("MBL-001", "p0l3"),
            "containers[0].gross_weight_kg": ("24.000", "p0l8"),
            "containers[0].number": ("ABCU9877666", "p0l6"),
            "containers[0].seal": ("SL345566", "p0l6"),
            "containers[0].package_count": ("125", "p0l6"),
            "containers[0].package_type": ("PACKAGES", "p0l6"),
        }
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
    fields = result.document.fields
    assert fields["bill_of_lading.number"].value == "MBL-001"
    assert fields["consignee.name"].value == "AL NASER TRADING COMPANY LLC"
    assert fields["containers[0].number"].value == "ABCU9877666"
    assert fields["containers[0].gross_weight"].value == "18,000.000"
    assert fields["containers[0].volume"].value == "24.000"
    assert "containers[0].gross_weight_kg" not in fields
    assert fields["containers[0].seal"].value == "SL345566"
    assert fields["containers[0].package_count"].value == "125"
    assert result.document.tables["containers"].row_count_extracted == 1
    assert [request.stage for request in provider.requests] == [
        "delivery_order_header_party",
        "delivery_order_containers",
    ]
    if printed_number:
        assert fields["delivery_order.number"].value == "DO-1001"
        assert result.document.status == "extracted", result.errors
    else:
        assert "delivery_order.number" not in fields
        assert "delivery_order.number:reference_label_mismatch" in result.errors
        assert result.document.status == "review_required"
