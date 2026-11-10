from copy import deepcopy

import pytest

from trade_extract.review.line_items import invoice_line_items


@pytest.mark.parametrize("document_type", ["commercial_invoice", "proforma_invoice"])
def test_invoice_items_group_by_numeric_index_without_changing_evidence(document_type):
    fields = {
        "seller.name": {"value": "Seller"},
        "line_items[10].description": {"value": "Last item"},
        "line_items[2].unit_price": {"value": "12.50"},
        "line_items[2].description": {"value": "First item"},
        "line_items[2].unit_code": {"value": "EA"},
        "line_items[2].custom_column": {"value": "Retained"},
    }
    before = deepcopy(fields)
    result = invoice_line_items(document_type, fields, {})
    assert result["scalar_fields"] == {"seller.name": fields["seller.name"]}
    table = result["line_item_table"]
    assert [row["index"] for row in table["rows"]] == [2, 10]
    first = table["rows"][0]["cells"]
    assert first["unit_price"]["path"] == "line_items[2].unit_price"
    assert first["unit_price"]["field"] is fields["line_items[2].unit_price"]
    assert "quantity" not in first  # Missing cells remain missing, never zero-filled.
    assert [col["label"] for col in table["columns"]] == [
        "Description",
        "Quantity",
        "Unit price",
        "Amount",
        "Unit",
        "Custom Column",
    ]
    assert fields == before


def test_line_item_empty_and_incomplete_states_do_not_invent_rows():
    result = invoice_line_items(
        "commercial_invoice",
        {},
        {"line_items": {"row_count_stated": 3, "unresolved_rows": [0, 1, 2]}},
    )
    assert result["line_item_table"]["rows"] == []
    assert result["line_item_table"]["incomplete"] is True
    assert len(result["line_item_table"]["columns"]) == 4


def test_other_document_types_keep_existing_flat_fields():
    fields = {"line_items[0].description": {"value": "Existing field"}}
    result = invoice_line_items("packing_list", fields, {})
    assert result["scalar_fields"] is fields
    assert result["line_item_table"] is None
