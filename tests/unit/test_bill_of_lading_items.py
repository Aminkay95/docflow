from dataclasses import replace

from tests.unit.test_invoice_phases import Scripted, fixture
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import OCREvidence, TableRegion, TableRow, TextCharacter
from trade_extract.review.line_items import document_line_items
from trade_extract.services.extraction import extract
from trade_extract.services.readers import TextLayerReader


def test_bill_of_lading_extracts_cargo_and_container_rows_separately():
    kind = "bill_of_lading"
    source, page, _ = fixture(kind)
    values = {
        "bl.number": "POLU2501150042",
        "bl.issue_date": "15 Jan 2025",
        "carrier.name": "Pacific Ocean Line",
        "shipper.name": "Pacific Trading Co.",
        "consignee.name": "Sydney Import Ltd.",
        "routing.port_of_loading": "Long Beach",
        "routing.port_of_discharge": "Sydney",
        "line_items[0].description": "Stainless Steel Cookware Sets",
        "line_items[1].description": "Non-stick Frying Pans 28cm",
        "line_items[2].description": "Kitchen Utensil Sets (5pc)",
        "containers[0].number": "OVRU7654326",
    }
    citations = {}
    characters = []
    rows = []
    for index, (path, value) in enumerate(values.items()):
        y = 0.02 * (index + 1)
        citations[path] = (value, f"p0l{index}")
        characters.extend(
            TextCharacter(
                char,
                BBox(x0=0.02 + col * 0.005, x1=0.024 + col * 0.005, y0=y, y1=y + 0.008),
            )
            for col, char in enumerate(value)
        )
        characters.append(TextCharacter("\n", BBox(x0=0, x1=0.001, y0=y, y1=y + 0.008)))
        rows.append(TableRow(BBox(x0=0, x1=1, y0=y, y1=y + 0.009)))
    cargo = TableRegion(
        "cargo", BBox(x0=0, x1=1, y0=0.15, y1=0.21), tuple(rows[7:10]), "line_items"
    )
    containers = TableRegion(
        "containers", BBox(x0=0, x1=1, y0=0.215, y1=0.24), (rows[10],), "containers"
    )
    page = replace(
        page,
        characters=tuple(characters),
        ocr=OCREvidence(0, "ok", tables=(cargo, containers)),
    )
    provider = Scripted(citations)
    result = extract(
        provider,
        document_type=kind,
        source=source,
        pages=(page,),
        readers=(TextLayerReader(),),
        max_repairs=0,
    )
    assert result.document is not None, result.errors
    assert result.document.status == "extracted", result.errors
    assert result.document.tables["line_items"].row_count_extracted == 3
    assert result.document.tables["containers"].row_count_extracted == 1
    assert [request.stage for request in provider.requests] == [
        "bill_of_lading_header_party",
        "bill_of_lading_containers",
        "bill_of_lading_line_items",
    ]
    projection = document_line_items(
        kind,
        result.document.fields,
        {name: table.model_dump() for name, table in result.document.tables.items()},
    )
    assert len(projection["line_item_table"]["rows"]) == 3
    assert (
        projection["table_panels"]["containers"]["rows"][0]["cells"]["number"]["path"]
        == "containers[0].number"
    )
