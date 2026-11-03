from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.integration.test_invoice_conflicts import scripted_extraction
from tests.integration.test_lifecycle import docs, drain, post_files
from tests.support import make_pdf
from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.domain.clock import FrozenClock

pytestmark = pytest.mark.integration


def test_accumulated_reads_keep_submission_scope_and_retained_evidence(
    api, tmp_path, monkeypatch, second_tenant
):
    scripted_extraction(tmp_path, monkeypatch, api, currencies=("USD", "EUR"))
    first = post_files(
        api,
        {"job_ref": "DAYS-APART"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Invoice 1 USD Total 10"]])),
    ).json()
    drain(api)
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        document.purged_at = datetime.now(UTC)
        session.get(File, document.file_id).status = "purged"
        job = session.get(Job, first["job_id"])
        job.status = "completed"
        job.terminal_at = datetime.now(UTC)
        job.terminal_reason = "completed"
        session.commit()
    api.app.state.clock = FrozenClock(datetime.now(UTC) + timedelta(days=3))
    second = post_files(
        api,
        {"job_ref": "DAYS-APART"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Invoice 2 EUR Total 10"]])),
    ).json()
    assert second["job_id"] == first["job_id"]
    assert second["result_id"] != first["result_id"]
    before = api.client.get(f"/v1/jobs/{first['job_id']}", headers=api.headers)
    assert before.json()["terminal_at"] is None
    drain(api)
    response = api.client.get(f"/v1/jobs/{first['job_id']}", headers=api.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["document_count"] == 2
    assert body["review"]["conflict_open"] == 1
    conflicts = api.client.get(f"/v1/jobs/{first['job_id']}/conflicts", headers=api.headers).json()[
        "data"
    ]
    currency = next(c for c in conflicts if c["canonical_key"] == "invoice.currency")
    assert {s["stated_value"] for s in currency["sources"]} == {"USD", "EUR"}
    filtered = api.client.get(
        f"/v1/jobs/{first['job_id']}/conflicts",
        params={"document_type": "commercial_invoice"},
        headers=api.headers,
    ).json()
    assert len(filtered["data"]) == 1
    assert (
        api.client.get(
            f"/v1/jobs/{first['job_id']}/conflicts",
            params={"document_type": "custom_entry"},
            headers=api.headers,
        ).json()["data"]
        == []
    )
    for accepted in (first, second):
        result = api.client.get(f"/v1/results/{accepted['result_id']}", headers=api.headers).json()
        assert len(result["documents"]) == 1
    url = f"/v1/jobs/{first['job_id']}/documents"
    page = api.client.get(url, params={"limit": 1}, headers=api.headers).json()
    assert page["has_more"] is True
    last = api.client.get(
        url, params={"limit": 1, "cursor": page["next_cursor"]}, headers=api.headers
    ).json()
    assert last["has_more"] is False
    assert last["data"][0]["document_id"] != page["data"][0]["document_id"]
    doc_id = page["data"][0]["document_id"]
    assert api.client.get(f"/v1/documents/{doc_id}", headers=api.headers).status_code == 200
    other_headers = {"Authorization": f"Bearer {second_tenant[0]}"}
    for path in (f"/v1/jobs/{first['job_id']}", url, f"/v1/documents/{doc_id}"):
        assert api.client.get(path, headers=other_headers).status_code == 404
    replay = post_files(
        api,
        {"job_ref": "DAYS-APART"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Invoice 2 EUR Total 10"]])),
    )
    assert replay.json()["duplicate_documents"] == 1
    assert (
        api.client.get(f"/v1/jobs/{first['job_id']}", headers=api.headers).json()["document_count"]
        == 2
    )
