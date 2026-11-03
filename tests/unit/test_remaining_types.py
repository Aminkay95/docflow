from copy import deepcopy
from dataclasses import replace

import pytest

from tests.contract.test_document_schemas import payload, required_fields
from tests.unit.test_extraction import fixture
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import ProviderResult
from trade_extract.providers.mock import MockProvider
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS
from trade_extract.services.conflicts import detect_conflicts
from trade_extract.services.extraction import extract

REMAINING = (
    "proforma_invoice",
    "bill_of_lading",
    "custom_entry",
    "delivery_order",
    "port_pass",
    "verification_memo",
)


@pytest.mark.parametrize("document_type", REMAINING)
def test_generic_extraction_validates_each_existing_type_and_skips_unreadable(
    tmp_path, document_type
):
    _, page, _ = fixture(tmp_path)
    source = EnvelopeSource(
        filename="document.pdf",
        content_type="application/pdf",
        file_hash="a" * 64,
        document_hash=document_hash(
            source_file_sha256="a" * 64, page_start=0, page_end=0, document_type=document_type
        ),
        page_start=0,
        page_end=0,
    )
    raw = (
        DOCUMENT_SCHEMAS[document_type]
        .model.model_validate(payload(document_type, required_fields(document_type)))
        .model_dump(mode="json")
    )
    raw["source"] = source.model_dump(mode="json")
    for field in raw["fields"].values():
        field["provenance"]["document_hash"] = source.document_hash
        field["provenance"]["reader"] = "vision"
    provider = MockProvider({(document_type, source.document_hash): [ProviderResult(raw, "mock")]})
    page = replace(page, characters=())
    result = extract(provider, document_type=document_type, source=source, pages=(page,))
    assert result.document is not None, result.errors
    assert result.document.document_type == document_type
    assert result.document.schema_version == DOCUMENT_SCHEMAS[document_type].version
    calls = len(provider.requests)
    refused = extract(
        provider,
        document_type=document_type,
        source=source,
        pages=(replace(page, unreadable=True),),
    )
    assert refused.status == "unreadable" and refused.document is None
    assert len(provider.requests) == calls


def component_documents(*, amount="110", currency="USD", term="CIF"):
    def field(value):
        return {
            "value": value,
            "provenance": {
                "page_index": 0,
                "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.2},
                "document_hash": "a" * 64,
            },
        }

    return [
        {
            "document_id": "invoice",
            "document_type": "commercial_invoice",
            "fields": {
                "totals.payable_amount": field("100"),
                "invoice.currency": field("USD"),
                "incoterm.code": field(term),
            },
        },
        {
            "document_id": "entry",
            "document_type": "custom_entry",
            "fields": {
                "consignment.cif_value": field(amount),
                "consignment.invoice_currency": field(currency),
            },
        },
    ]


def test_customs_printed_values_only_compare_explicit_matching_components():
    documents = component_documents()
    original = deepcopy(documents)
    conflicts = detect_conflicts("job_1", documents)
    assert len(conflicts) == 1 and conflicts[0].canonical_key == "customs.cif_amount"
    assert {s["stated_value"] for s in conflicts[0].sources} == {"100", "110"}
    assert documents == original
    assert not detect_conflicts("job_1", component_documents(amount="100"))
    assert not detect_conflicts("job_1", component_documents(term="FOB"))
    assert not detect_conflicts("job_1", component_documents(term=""))
    mismatch = detect_conflicts("job_1", component_documents(currency="EUR"))
    assert all(s["comparison_reason"] == "incomparable_currency" for s in mismatch[0].sources)
    missing = component_documents()
    del missing[1]["fields"]["consignment.invoice_currency"]
    incomparable = detect_conflicts("job_1", missing)
    assert any(s["comparison_reason"] for s in incomparable[0].sources)
