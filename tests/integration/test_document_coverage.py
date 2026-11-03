from copy import deepcopy

import pytest
from sqlalchemy import select

from tests.contract.test_document_schemas import (
    identity,
    invoice_with_lines,
    leaf,
    payload,
    required_fields,
    table_meta,
)
from tests.integration.test_lifecycle import docs, drain, post_files
from tests.support import make_pdf
from trade_extract.db.models.document import Document
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.trust import TrustObservation
from trade_extract.domain.inference import ExtractionOutcome
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS
from trade_extract.workers.handlers import extract_segment

pytestmark = pytest.mark.integration
TYPES = [
    ("proforma_invoice", "PROFORMA INVOICE"),
    ("bill_of_lading", "BILL OF LADING"),
    ("custom_entry", "CUSTOMS ENTRY"),
    ("delivery_order", "DELIVERY ORDER"),
    ("port_pass", "PORT PASS"),
    ("verification_memo", "VERIFICATION MEMO"),
]


def type_payload(document_type):
    if document_type == "proforma_invoice":
        return invoice_with_lines(rows=(0, 1), document_type=document_type)
    fields = required_fields(document_type)
    extension = {
        "extension.any.notes": leaf(
            "Handwritten free-form detail, retained as printed.",
            document_hash_value=identity(document_type),
        )
    }
    tables = {}
    if document_type == "custom_entry":
        fields.update(
            {
                f"goods_lines[{i}].description": leaf(
                    f"Goods row {i}", document_hash_value=identity(document_type)
                )
                for i in range(2)
            }
        )
        tables["goods_lines"] = table_meta(2, 2)
    if document_type == "verification_memo":
        for i, text in enumerate(
            (
                "Seal intact; officer initialled beside stamp.",
                "One carton torn / see handwritten observation — no shortage stated.",
            )
        ):
            fields[f"findings[{i}].text"] = leaf(text, document_hash_value=identity(document_type))
        tables["findings"] = table_meta(2, 2)
    return payload(document_type, fields, tables=tables, extension=extension)


@pytest.mark.parametrize(("document_type", "heading"), TYPES)
def test_registered_types_activate_and_persist_provenance_under_full_review(
    api, monkeypatch, document_type, heading
):
    calls = []
    original = type_payload(document_type)

    def scripted(provider, *, source, pages, segment_id, **kwargs):
        calls.append(kwargs["document_type"])
        raw = deepcopy(original)
        raw["source"] = source.model_dump(mode="json")
        raw["segment_id"] = segment_id
        for field in [*raw["fields"].values(), *raw["extension"].values()]:
            if hasattr(field, "model_dump"):
                raise AssertionError("fixture must be serialized before rehashing")
            field["provenance"]["document_hash"] = source.document_hash
            field["provenance"]["page_index"] = source.page_start
        model = DOCUMENT_SCHEMAS[document_type].model.model_validate(raw)
        return ExtractionOutcome("extracted", document=model)

    # Convert independent test fields into the wire representation.
    original = (
        DOCUMENT_SCHEMAS[document_type].model.model_validate(original).model_dump(mode="json")
    )
    monkeypatch.setattr(extract_segment, "extract", scripted)
    with api.session_factory() as session:
        policy = session.scalar(select(Policy))
        policy.config = {"invoice_pass_enabled": True, "review_depth": "checks"}
        session.commit()
    response = post_files(
        api,
        {"job_ref": f"COVER-{document_type}"},
        docs(
            make_pdf(
                [
                    [
                        heading,
                        "Document 123 Issued 2026-11-03",
                        "Exporter Importer Cargo Goods",
                        "Total 10 USD",
                    ]
                ]
            )
        ),
    )
    assert response.status_code == 202, response.text
    drain(api)
    assert calls == [document_type]
    result = api.client.get(
        f"/v1/results/{response.json()['result_id']}", headers=api.headers
    ).json()
    document = result["documents"][0]
    assert document["status"] == "review_required"
    assert document["schema_version"] == DOCUMENT_SCHEMAS[document_type].version
    assert document["checks"] == []  # No invoice arithmetic is run on other types.
    assert "review_depth:full" in document["decision"]["reasons"]
    assert result["review"] == {"extraction_open": 1, "conflict_open": 0}
    for field in [*document["fields"].values(), *document["extension"].values()]:
        assert field["provenance"]["document_hash"] == document["source"]["document_hash"]
        assert field["provenance"]["bbox"]["x1"] > field["provenance"]["bbox"]["x0"]
    if document_type in ("proforma_invoice", "custom_entry", "verification_memo"):
        table = next(iter(document["tables"].values()))
        assert table["row_count_extracted"] == table["row_count_stated"] == 2
    with api.session_factory() as session:
        assert session.scalar(select(TrustObservation.document_id)) == document["document_id"]
        assert session.scalar(select(Document)).extraction["fields"] == document["fields"]
