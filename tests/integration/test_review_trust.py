import json
import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.integration.test_invoice_conflicts import scripted_extraction
from tests.integration.test_lifecycle import docs, drain, post_files
from tests.support import make_pdf
from trade_extract.db.models.client import Client
from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import ConflictRecord, ReviewItem
from trade_extract.db.models.job import Job
from trade_extract.db.models.review import ReviewResolution
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.models.trust import TrustObservation, TrustRecord
from trade_extract.domain.clock import FrozenClock
from trade_extract.review.auth import password_hash

pytestmark = pytest.mark.integration


def login(api, role="reviewer", username="reviewer"):
    with api.session_factory() as session:
        row = session.scalar(select(Reviewer).where(Reviewer.username == username))
        if row is None:
            session.add(
                Reviewer(
                    id=f"usr_{username}",
                    username=username,
                    password_hash=password_hash("test-password"),
                    display_name=username,
                    role=role,
                    is_active=True,
                    session_epoch=0,
                )
            )
            session.commit()
    page = api.client.get("/review/login")
    token = re.search(r'name="csrf" type="hidden" value="([^"]+)"', page.text)[1]
    response = api.client.post(
        "/review/login",
        data={"username": username, "password": "test-password", "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    dashboard = api.client.get("/review")
    assert dashboard.status_code == 200
    csrf = re.search(r'id="csrf" type="hidden" value="([^"]+)"', dashboard.text)[1]
    return {"X-CSRF-Token": csrf}


def invoices(api, tmp_path, monkeypatch, currencies=("USD",)):
    scripted_extraction(tmp_path, monkeypatch, api, currencies=currencies)
    response = post_files(
        api,
        {"job_ref": "REVIEW-JOB"},
        docs(
            *[
                make_pdf([["COMMERCIAL INVOICE", f"Seller Buyer Goods Invoice {i} Total 10"]])
                for i in range(len(currencies))
            ]
        ),
    )
    assert response.status_code == 202
    drain(api)
    return response.json()


def test_extraction_corrections_are_labels_with_separate_public_overlay(api, tmp_path, monkeypatch):
    accepted = invoices(api, tmp_path, monkeypatch)
    headers = login(api)
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        original = deepcopy(document.extraction)
        document_id = document.id
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.track == "extraction"))
    url = f"/review/queues/{item_id}/resolve"
    field = deepcopy(original["fields"]["invoice.number"])
    field["value"] = "INV-CORRECTED"
    response = api.client.post(
        url,
        json={
            "outcome": "corrected",
            "reason": "Read against source",
            "field_path": "invoice.number",
            "corrected_value": field,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    with api.session_factory() as session:
        document = session.get(Document, document_id)
        assert document.extraction == original
        label = session.scalar(select(ReviewResolution))
        assert label.prior_value == original["fields"]["invoice.number"]
        assert label.corrected_value["value"] == "INV-CORRECTED"
        assert label.corrected_value["agreement"] == "human_corrected"
        trust = session.scalar(select(TrustRecord))
        assert trust.correction_count == 1 and trust.review_depth == "full"
        assert session.get(Job, accepted["job_id"]).open_review_count == 1
    assert (
        api.client.post(
            url,
            json={
                "outcome": "confirmed",
                "reason": "Already checked",
                "field_path": "invoice.number",
            },
            headers=headers,
        ).status_code
        == 409
    )
    for path in set(original["fields"]) - {"invoice.number"}:
        result = api.client.post(
            url,
            json={"outcome": "confirmed", "reason": "Matches source", "field_path": path},
            headers=headers,
        )
        assert result.status_code == 200, result.text
    body = api.client.get(f"/v1/results/{accepted['result_id']}", headers=api.headers).json()
    output = body["documents"][0]
    assert output["status"] == "resolved"
    assert output["fields"]["invoice.number"]["value"] != "INV-CORRECTED"
    assert output["corrections"]["invoice.number"]["value"] == "INV-CORRECTED"
    assert body["review"] == {"extraction_open": 0, "conflict_open": 0}
    workspace = api.client.get(f"/review/documents/{document_id}")
    assert workspace.status_code == 200, workspace.text
    assert "Human correction: INV-CORRECTED" in workspace.text
    with api.session_factory() as session:
        job = session.get(Job, accepted["job_id"])
        assert job.status == "completed" and job.terminal_at is not None


@pytest.mark.parametrize("decision", ["accepted", "waived"])
def test_conflict_choices_are_distinct_and_replay_does_not_reopen(
    api, tmp_path, monkeypatch, decision
):
    accepted = invoices(api, tmp_path, monkeypatch, ("USD", "EUR"))
    headers = login(api)
    with api.session_factory() as session:
        conflict = session.scalar(select(ConflictRecord))
        conflict_id = conflict.id
        fingerprint = conflict.fingerprint
        source_id = session.scalar(select(Document.id).order_by(Document.id))
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.track == "conflict"))
        original = [
            deepcopy(d.extraction) for d in session.scalars(select(Document).order_by(Document.id))
        ]
    assert api.client.get("/review").text.count('class="count"') == 2
    queue = api.client.get("/review/queues/conflicts")
    assert "Source documents disagree" in queue.text
    assert "review_depth:full" not in queue.text
    url = f"/review/queues/{item_id}/resolve"
    assert (
        api.client.post(
            url,
            json={
                "outcome": "accepted",
                "reason": "Client approved",
                "chosen_document_id": "doc_other",
                "expected_fingerprint": fingerprint,
            },
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        api.client.post(
            url,
            json={
                "outcome": "client_notified",
                "reason": "Awaiting client",
                "expected_fingerprint": fingerprint,
            },
            headers=headers,
        ).status_code
        == 200
    )
    with api.session_factory() as session:
        assert session.get(ConflictRecord, conflict_id).status == "open"
        assert session.get(Job, accepted["job_id"]).open_conflict_count == 1
    assert (
        api.client.post(
            url,
            json={
                "outcome": decision,
                "reason": "Client accepts discrepancy",
                "expected_fingerprint": fingerprint,
                **({"chosen_document_id": source_id} if decision == "accepted" else {}),
            },
            headers=headers,
        ).status_code
        == 200
    )
    with api.session_factory() as session:
        assert session.get(Job, accepted["job_id"]).open_conflict_count == 0
        assert session.get(Job, accepted["job_id"]).open_review_count == 2
        labels = list(session.scalars(select(ReviewResolution)))
        assert {label.outcome for label in labels} == {decision, "client_notified"}
        final = next(label for label in labels if label.outcome == decision)
        assert final.chosen_document_id == (source_id if decision == "accepted" else None)
        assert session.get(ConflictRecord, conflict_id).status == (
            "resolved" if decision == "accepted" else "waived"
        )
        assert all(
            label.field_path == "invoice.currency" and len(label.prior_value["sources"]) == 2
            for label in labels
        )
        assert [
            d.extraction for d in session.scalars(select(Document).order_by(Document.id))
        ] == original
    workspace = api.client.get(f"/review/conflicts/{conflict_id}")
    assert workspace.status_code == 200
    assert "Awaiting client" in workspace.text and "Client accepts discrepancy" in workspace.text
    assert "usr_reviewer" in workspace.text
    from trade_extract.services.jobs import job_result

    with api.session_factory() as session:
        handoff = job_result(session, accepted["job_id"], api.client_id)
    resolution = next(row for row in handoff["review_resolutions"] if row["outcome"] == decision)
    assert resolution["chosen_document_id"] == (source_id if decision == "accepted" else None)
    assert resolution["reason"] == "Client accepts discrepancy"
    # Duplicate-only submission reconciles the exact same retained evidence.
    again = post_files(
        api,
        {"job_ref": "REVIEW-JOB"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Invoice 0 Total 10"]])),
    )
    assert again.status_code == 202
    assert (
        api.client.get(f"/v1/jobs/{accepted['job_id']}", headers=api.headers).json()["review"][
            "conflict_open"
        ]
        == 0
    )


def test_review_auth_csrf_roles_media_and_session_expiry(api, tmp_path, monkeypatch):
    invoices(api, tmp_path, monkeypatch)
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        document_id = document.id
        item_id = session.scalar(select(ReviewItem.id))
    media = f"/review/media/documents/{document_id}/pages/0"
    assert api.client.get(media, headers=api.headers).status_code == 401
    api.client.get("/review/login")
    api.client.cookies.set("review_session", api.client.cookies.get("review_login"))
    assert api.client.get("/review").status_code == 401
    api.client.cookies.clear()
    headers = login(api)
    assert api.client.get(media).status_code == 200
    assert api.client.get(media + "/thumb").status_code == 200
    assert api.client.get(media.replace("/0", "/99")).status_code == 404
    url = f"/review/queues/{item_id}/resolve"
    assert (
        api.client.post(
            url,
            json={
                "outcome": "confirmed",
                "reason": "Matches source",
                "field_path": "invoice.number",
            },
        ).status_code
        == 403
    )
    assert (
        api.client.post(
            url,
            json={"outcome": "confirmed", "reason": "x", "field_path": "invoice.number"},
            headers=headers,
        ).status_code
        == 400
    )
    api.client.cookies.clear()
    auditor = login(api, role="auditor", username="auditor")
    assert (
        api.client.post(
            url,
            json={
                "outcome": "confirmed",
                "reason": "Matches source",
                "field_path": "invoice.number",
            },
            headers=auditor,
        ).status_code
        == 403
    )
    assert "Record field resolution" not in api.client.get(f"/review/documents/{document_id}").text
    assert (
        api.client.post("/review/logout", headers=auditor, follow_redirects=False).status_code
        == 303
    )
    headers = login(api)
    with api.session_factory() as session:
        session.get(Reviewer, "usr_reviewer").session_epoch += 1
        session.commit()
    assert api.client.get("/review").status_code == 401
    login(api)
    api.app.state.clock = FrozenClock(datetime.now(UTC) + timedelta(minutes=31))
    assert api.client.get("/review").status_code == 401


def test_trust_identity_is_retry_safe_and_corrections_demote_only_matching_record(
    api, tmp_path, monkeypatch
):
    from trade_extract.services.trust import demote, observe

    invoices(api, tmp_path, monkeypatch, ("USD", "USD"))
    with api.session_factory() as session:
        documents = list(session.scalars(select(Document).order_by(Document.id)))
        records = list(session.scalars(select(TrustRecord)))
        assert len(records) == 1  # Identical layout; values differ only in numeric invoice ID.
        assert records[0].state == "unseen" and records[0].review_depth == "full"
        assert len(list(session.scalars(select(TrustObservation)))) == 2
        observe(session, documents[0], datetime.now(UTC))
        session.flush()
        assert len(list(session.scalars(select(TrustObservation)))) == 2
        records[0].state = "trusted"
        records[0].review_depth = "sampled"
        # A different template must not share correction state.
        session.delete(session.get(TrustObservation, documents[1].id))
        session.flush()
        documents[1].template_fingerprint = "different-template"
        second = observe(session, documents[1], datetime.now(UTC))
        session.flush()
        demote(session, documents[0], datetime.now(UTC))
        assert records[0].state == "unseen" and records[0].review_depth == "full"
        assert records[0].correction_count == 1
        assert second.correction_count == 0 and second.state == "unseen"


def test_shared_trust_creation_is_atomic_across_concurrent_documents(api, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import delete

    from trade_extract.services.trust import observe

    invoices(api, tmp_path, monkeypatch, ("USD", "USD"))
    with api.session_factory() as session:
        ids = list(session.scalars(select(Document.id)))
        session.execute(delete(TrustObservation))
        session.execute(delete(TrustRecord))
        session.commit()
    barrier = Barrier(2)

    def observe_one(document_id):
        with api.session_factory() as session:
            document = session.get(Document, document_id)
            barrier.wait(timeout=10)
            result = observe(session, document, datetime.now(UTC))
            session.commit()
            return result.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(observe_one, ids))
    assert len(set(results)) == 1
    with api.session_factory() as session:
        assert len(list(session.scalars(select(TrustRecord)))) == 1
        assert len(list(session.scalars(select(TrustObservation)))) == 2
    from trade_extract.services.trust import demote

    def correct_one(document_id):
        with api.session_factory() as session:
            demote(session, session.get(Document, document_id), datetime.now(UTC))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(correct_one, ids))
    with api.session_factory() as session:
        assert session.scalar(select(TrustRecord)).correction_count == 2


def test_invalid_corrections_claims_and_extension_labels(api, tmp_path, monkeypatch):
    from trade_extract.domain.hashing import stable_json

    invoices(api, tmp_path, monkeypatch)
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        snapshot = deepcopy(document.extraction)
        extension_field = deepcopy(snapshot["fields"]["invoice.number"])
        extension_field["value"] = "Stamped note"
        extension_field["value_type"] = "string"
        snapshot["extension"]["extension.any.note"] = extension_field
        document.extraction = snapshot
        item_id = session.scalar(select(ReviewItem.id))
        session.commit()
    headers = login(api)
    claim = f"/review/queues/{item_id}/claim"
    assert api.client.post(claim, headers=headers).status_code == 200
    other = login(api, username="other")
    assert api.client.post(claim, headers=other).status_code == 409
    url = f"/review/queues/{item_id}/resolve"
    assert (
        api.client.post(
            url,
            json={
                "outcome": "confirmed",
                "reason": "Matches source",
                "field_path": "invoice.number",
            },
            headers=other,
        ).status_code
        == 409
    )
    headers = login(api)
    invalid = deepcopy(extension_field)
    invalid["provenance"]["bbox"]["x1"] = 0.99
    bad = api.client.post(
        url,
        json={
            "outcome": "corrected",
            "reason": "Source checked",
            "field_path": "extension.any.note",
            "corrected_value": invalid,
        },
        headers=headers,
    )
    assert bad.status_code == 400
    invalid = {**extension_field, "value_type": "integer"}
    assert (
        api.client.post(
            url,
            json={
                "outcome": "corrected",
                "reason": "Source checked",
                "field_path": "extension.any.note",
                "corrected_value": invalid,
            },
            headers=headers,
        ).status_code
        == 400
    )
    assert (
        api.client.post(
            url,
            json={
                "outcome": "confirmed",
                "reason": "Source checked",
                "field_path": "invented.path",
            },
            headers=headers,
        ).status_code
        == 400
    )
    corrected = {**extension_field, "value": "Corrected stamp"}
    response = api.client.post(
        url,
        json={
            "outcome": "corrected",
            "reason": "Source checked",
            "field_path": "extension.any.note",
            "corrected_value": corrected,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    with api.session_factory() as session:
        assert session.scalar(select(ReviewResolution)).field_path == "extension.any.note"
        assert stable_json(session.scalar(select(Document)).extraction) == stable_json(snapshot)
        assert session.scalar(select(TrustRecord)).correction_count == 1


@pytest.mark.parametrize("outcome", ["unreadable_confirmed", "failure_confirmed"])
def test_empty_failures_can_be_acknowledged_without_inventing_values(api, monkeypatch, outcome):
    from trade_extract.domain.inference import ExtractionOutcome
    from trade_extract.workers.handlers import extract_segment

    status = "unreadable" if outcome == "unreadable_confirmed" else "extraction_failed"
    monkeypatch.setattr(extract_segment, "extract", lambda *a, **kw: ExtractionOutcome(status))
    accepted = post_files(
        api,
        {"job_ref": "NO-VALUES"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Invoice Buyer Seller Goods USD Total Ten"]])),
    ).json()
    drain(api)
    headers = login(api)
    with api.session_factory() as session:
        item_id = session.scalar(select(ReviewItem.id))
        document_id = session.scalar(select(Document.id))
    assert (
        api.client.post(
            f"/review/documents/{document_id}/confirm-remaining", headers=headers
        ).status_code
        == 400
    )
    page = api.client.get(f"/review/jobs/{accepted['job_id']}")
    assert (
        "Confirm unreadable outcome" in page.text
        if outcome == "unreadable_confirmed"
        else "Confirm failed extraction outcome" in page.text
    )
    response = api.client.post(
        f"/review/queues/{item_id}/resolve",
        headers=headers,
        json={"outcome": outcome, "reason": "Source cannot be reliably read"},
    )
    assert response.status_code == 200, response.text
    result = api.client.get(f"/v1/results/{accepted['result_id']}", headers=api.headers).json()
    assert result["documents"][0]["fields"] is None
    assert result["review"]["extraction_open"] == 0
    assert result["progress"]["documents_completed"] == 0
    assert result["progress"]["documents_failed"] == 1
    with api.session_factory() as session:
        assert session.scalar(select(ReviewResolution)).prior_value["status"] in (
            "failed",
            "unreadable",
        )


def test_changed_conflict_evidence_rejects_stale_resolution(api, tmp_path, monkeypatch):
    invoices(api, tmp_path, monkeypatch, ("USD", "EUR"))
    headers = login(api)
    with api.session_factory() as session:
        conflict = session.scalar(select(ConflictRecord))
        old_fingerprint = conflict.fingerprint
        conflict_id = conflict.id
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.track == "conflict"))
    assert api.client.get(f"/review/conflicts/{conflict_id}").status_code == 200
    post_files(
        api,
        {"job_ref": "REVIEW-JOB"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Invoice 2 Total 10"]])),
    )
    drain(api)
    response = api.client.post(
        f"/review/queues/{item_id}/resolve",
        headers=headers,
        json={
            "outcome": "waived",
            "reason": "Old evidence approved",
            "expected_fingerprint": old_fingerprint,
        },
    )
    assert response.status_code == 409
    with api.session_factory() as session:
        assert session.get(ConflictRecord, conflict_id).fingerprint != old_fingerprint
        assert session.get(ConflictRecord, conflict_id).status == "open"
        assert session.scalar(select(ReviewResolution.id)) is None


def test_login_rate_limits_both_windows_and_expires_unused_buckets(api):
    base = datetime.now(UTC)
    api.app.state.clock = FrozenClock(base)

    def attempt(username):
        page = api.client.get("/review/login")
        token = re.search(r'name="csrf" type="hidden" value="([^"]+)"', page.text)[1]
        return api.client.post(
            "/review/login",
            data={"username": username, "password": "wrong-password", "csrf": token},
            follow_redirects=False,
        )

    for i in range(5):
        assert attempt(f"unused-{i}").status_code == 401
    assert attempt("unused-six").status_code == 429
    api.app.state.clock = FrozenClock(base + timedelta(seconds=61))
    assert attempt("after-ip-window").status_code == 401
    # Space the requests to isolate the per-username limit from the IP limit.
    for i in range(10):
        api.app.state.clock = FrozenClock(base + timedelta(seconds=122 + 61 * i))
        assert attempt("same-user").status_code == 401
    api.app.state.clock = FrozenClock(base + timedelta(seconds=793))
    assert attempt("same-user").status_code == 429
    api.app.state.clock = FrozenClock(base + timedelta(hours=2))
    assert attempt("new-window").status_code == 401
    assert not any(key.startswith("user:unused-") for key in api.app.state.review_login_attempts)
    assert "user:same-user" not in api.app.state.review_login_attempts


def test_review_queue_can_reach_items_after_the_first_hundred(api, tmp_path, monkeypatch):
    accepted = invoices(api, tmp_path, monkeypatch)
    with api.session_factory() as session:
        for i in range(101):
            session.add(
                ConflictRecord(
                    id=f"cfl_queue{i:03}",
                    job_id=accepted["job_id"],
                    client_id=api.client_id,
                    canonical_key=f"test.field{i}",
                    field_label=f"Field {i}",
                    fingerprint=f"fingerprint{i}",
                    status="open",
                )
            )
        session.flush()
        for i in range(101):
            session.add(
                ReviewItem(
                    id=f"rev_queue{i:03}",
                    job_id=accepted["job_id"],
                    conflict_id=f"cfl_queue{i:03}",
                    track="conflict",
                    status="open",
                    reason_code="test",
                    summary=f"queue-entry{i:03}",
                    created_at=datetime.now(UTC) + timedelta(seconds=i),
                )
            )
        session.commit()
    login(api)
    page = api.client.get("/review/queues/conflicts")
    assert "queue-entry099" in page.text and "queue-entry100" not in page.text
    next_cursor = re.search(r'href="/review/queues/conflicts\?cursor=([^"]+)"', page.text)[1]
    last = api.client.get("/review/queues/conflicts", params={"cursor": next_cursor})
    assert "queue-entry100" in last.text
    assert "Next page" not in last.text


def test_resolution_does_not_overwrite_a_concurrent_failed_job(api, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from trade_extract.review import routes

    accepted = invoices(api, tmp_path, monkeypatch)
    headers = login(api)
    with api.session_factory() as session:
        item_id = session.scalar(select(ReviewItem.id))
    before_lock, may_continue = Event(), Event()
    original = routes.resolve

    def delayed(*args, **kwargs):
        # The route has already read the job into its identity map.
        before_lock.set()
        assert may_continue.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(routes, "resolve", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            api.client.post,
            f"/review/queues/{item_id}/resolve",
            headers=headers,
            json={
                "outcome": "confirmed",
                "reason": "Verified against source",
                "field_path": "invoice.number",
            },
        )
        assert before_lock.wait(timeout=10)
        with api.session_factory() as session:
            job = session.get(Job, accepted["job_id"])
            job.status = "failed"
            session.commit()
        may_continue.set()
        assert future.result(timeout=15).status_code == 200
    with api.session_factory() as session:
        assert session.get(Job, accepted["job_id"]).status == "failed"


def test_internal_upload_creates_job_and_shows_extracted_values(api, tmp_path, monkeypatch):
    scripted_extraction(tmp_path, monkeypatch, api)
    headers = login(api)
    response = api.client.post(
        "/review/jobs/new",
        headers=headers,
        data={"job_ref": "UI-UPLOAD"},
        files=docs(
            make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods", "Invoice USD Total 10"]])
        ),
    )
    assert response.status_code == 201, response.text
    accepted = response.json()
    assert accepted["submitted_documents"] == 1
    pending = api.client.get(accepted["job_url"])
    assert pending.status_code == 200
    assert 'data-autorefresh="4000"' in pending.text
    drain(api)

    page = api.client.get(accepted["job_url"])
    assert page.status_code == 200
    assert "Commercial Invoice" in page.text
    assert "Extracted fields" in page.text
    assert "invoice.number" in page.text
    assert "totals.payable_amount" in page.text
    assert "UI-UPLOAD" in page.text
    assert 'class="inline-field-row"' in page.text
    assert 'class="inline-field-value"' in page.text
    assert "<summary>Correct value</summary>" not in page.text
    assert 'class="line-items-table"' in page.text
    assert '<th scope="col">Description</th>' in page.text
    assert '<th scope="col">Quantity</th>' in page.text
    assert '<th scope="col">Unit price</th>' in page.text
    assert '<th scope="col">Amount</th>' in page.text
    assert 'data-autorefresh="4000"' not in page.text


def test_internal_upload_requires_session_csrf_and_write_permission(api):
    assert api.client.get("/review/jobs/new").status_code == 401
    assert api.client.post("/review/jobs/new").status_code == 401
    login(api)
    assert api.client.post("/review/jobs/new").status_code == 403
    auditor = login(api, role="auditor", username="upload-auditor")
    assert api.client.get("/review/jobs/new").status_code == 403
    assert api.client.post("/review/jobs/new", headers=auditor).status_code == 403
    for path in ("/review", "/review/jobs"):
        page = api.client.get(path)
        assert page.status_code == 200
        assert 'href="/review/jobs/new"' not in page.text


def test_internal_upload_rejects_invalid_file_without_creating_job(api):
    headers = login(api)
    response = api.client.post(
        "/review/jobs/new",
        headers=headers,
        files=[("documents", ("bad.pdf", b"not a PDF", "application/pdf"))],
    )
    assert response.status_code == 415, response.text
    assert response.json()["error"]["code"] == "unsupported_media_type"
    with api.session_factory() as session:
        assert session.scalar(select(Job.id)) is None


def test_internal_upload_retry_reuses_submission_without_job_reference(api):
    headers = {**login(api), "Idempotency-Key": "internal-upload-retry"}
    pdf = make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Invoice Total 10"]])
    first = api.client.post("/review/jobs/new", headers=headers, files=docs(pdf))
    second = api.client.post("/review/jobs/new", headers=headers, files=docs(pdf))
    assert first.status_code == second.status_code == 201
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["result_id"] == second.json()["result_id"]


def test_internal_upload_explains_inactive_clients(api):
    login(api)
    with api.session_factory() as session:
        session.get(Client, api.client_id).status = "suspended"
        session.commit()
    page = api.client.get("/review/jobs/new")
    assert page.status_code == 200
    assert "No active clients are available" in page.text
    assert "automatically creates" not in page.text
    assert re.search(r"data-submit[^>]*\bdisabled\b", page.text)


def test_job_field_summary_includes_extension_values():
    from trade_extract.review.routes import _field_rows

    document = Document(
        extraction={
            "fields": {"invoice.number": {"value": "INV-17"}},
            "extension": {
                "shipment.custom_reference": {"value": "REF-42", "provenance": {"page_index": 0}}
            },
        }
    )
    rows = {row["path"]: row for row in _field_rows(document)}
    assert rows["invoice.number"]["value"] == "INV-17"
    assert rows["shipment.custom_reference"]["value"] == "REF-42"
    assert rows["shipment.custom_reference"]["page"] == 1


def test_job_done_prints_reviewed_json_only_after_required_reviews(
    api, tmp_path, monkeypatch, capsys
):
    accepted = invoices(api, tmp_path, monkeypatch)
    pipeline_lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"event": "pipeline.result"')
    ]
    assert len(pipeline_lines) == 1
    assert pipeline_lines[0]["result"]["job_id"] == accepted["job_id"]
    headers = login(api)
    url = f"/review/jobs/{accepted['job_id']}/done"
    assert api.client.post(url, headers=headers).status_code == 409
    assert '"event": "review.result"' not in capsys.readouterr().out
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        document_id = document.id
        snapshot = deepcopy(document.extraction)
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.track == "extraction"))
    fields = {**snapshot["fields"], **snapshot.get("extension", {})}
    for path, field in fields.items():
        payload = {"outcome": "confirmed", "reason": "Checked against source", "field_path": path}
        if path == "invoice.number":
            payload["outcome"] = "corrected"
            payload["corrected_value"] = {**field, "value": "INV-REVIEWED"}
        response = api.client.post(
            f"/review/queues/{item_id}/resolve", headers=headers, json=payload
        )
        assert response.status_code == 200, response.text
    page = api.client.get(f"/review/jobs/{accepted['job_id']}")
    assert page.status_code == 200
    assert "data-finish-job=" in page.text
    assert "disabled>Done / Push result" not in page.text
    assert "View result JSON" in page.text
    response = api.client.post(url, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "completed"
    output = next(row for row in result["documents"] if row["document_id"] == document_id)
    assert output["effective_fields"]["invoice.number"]["value"] == "INV-REVIEWED"
    assert output["fields"]["invoice.number"] == snapshot["fields"]["invoice.number"]
    correction = next(row for row in result["review_resolutions"] if row["outcome"] == "corrected")
    assert correction["reviewer_id"] == "usr_reviewer"
    assert correction["reason"] == "Checked against source"
    printed = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"event": "review.result"')
    ]
    assert printed == [{"event": "review.result", "result": result}]
    assert response.headers["cache-control"] == "no-store"


def test_job_done_requires_write_session_and_csrf(api):
    url = "/review/jobs/job_missing/done"
    assert api.client.post(url).status_code == 401
    headers = login(api)
    assert api.client.post(url).status_code == 403
    assert api.client.post(url, headers=headers).status_code == 404
    auditor = login(api, role="auditor", username="done-auditor")
    assert api.client.post(url, headers=auditor).status_code == 403


def test_job_workspace_bulk_preserves_corrections_and_selection(api, tmp_path, monkeypatch):
    accepted = invoices(api, tmp_path, monkeypatch, currencies=("USD", "USD"))
    headers = login(api)
    with api.session_factory() as session:
        documents = list(
            session.scalars(select(Document).order_by(Document.created_at, Document.id))
        )
        first, second = documents
        first_id, second_id = first.id, second.id
        snapshot = deepcopy(first.extraction)
        snapshot["extension"]["extension.any.note"] = {
            **deepcopy(snapshot["fields"]["invoice.number"]),
            "value": "Stamped note",
            "value_type": "string",
        }
        first.extraction = snapshot
        session.commit()
        original = deepcopy(first.extraction)
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.document_id == first_id))
    corrected = deepcopy(original["fields"]["invoice.number"])
    corrected["value"] = "REVIEWED-NUMBER"
    assert (
        api.client.post(
            f"/review/queues/{item_id}/resolve",
            headers=headers,
            json={
                "outcome": "corrected",
                "field_path": "invoice.number",
                "reason": "Checked source",
                "corrected_value": corrected,
            },
        ).status_code
        == 200
    )
    url = f"/review/documents/{first_id}/confirm-remaining"
    assert api.client.post(url).status_code == 403
    result = api.client.post(url, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["confirmed"] == len(original["fields"]) + len(original["extension"]) - 1
    assert api.client.post(url, headers=headers).json() == {"confirmed": 0}
    page = api.client.get(f"/review/jobs/{accepted['job_id']}?document_id={first_id}&page=999")
    assert page.status_code == 200, page.text
    assert "REVIEWED-NUMBER" in page.text and "Original value" in page.text
    assert f'data-document-id="{first_id}"' in page.text
    assert 'id="page-0"' in page.text
    default = api.client.get(f"/review/jobs/{accepted['job_id']}")
    assert f'data-document-id="{second_id}"' in default.text
    assert (
        api.client.get(f"/review/jobs/{accepted['job_id']}?document_id=foreign").status_code == 404
    )
    with api.session_factory() as session:
        assert session.get(Document, first_id).extraction == original
        labels = list(
            session.scalars(
                select(ReviewResolution).where(ReviewResolution.review_item_id == item_id)
            )
        )
        assert len(labels) == len(original["fields"]) + len(original["extension"])
        assert sum(label.outcome == "corrected" for label in labels) == 1
    login(api, role="auditor", username="bulk-auditor")
    auditor_page = api.client.get(f"/review/jobs/{accepted['job_id']}")
    assert "Needs review" in auditor_page.text
    assert "Save correction" not in auditor_page.text
    assert 'class="page-head"' not in auditor_page.text
    assert 'class="btn btn-secondary btn-sm topbar-back" href="/review/jobs"' in auditor_page.text
    assert f"Job {accepted['job_id']}" not in auditor_page.text
    assert (
        api.client.post(
            f"/review/documents/{second_id}/confirm-remaining",
            headers={
                "X-CSRF-Token": re.search(
                    r'id="csrf" type="hidden" value="([^"]+)"', api.client.get("/review").text
                )[1],
            },
        ).status_code
        == 403
    )


def test_bulk_confirmation_ownership_and_atomic_rollback(api, tmp_path, monkeypatch):
    invoices(api, tmp_path, monkeypatch)
    headers = login(api)
    with api.session_factory() as session:
        item = session.scalar(select(ReviewItem).where(ReviewItem.track == "extraction"))
        document_id, item_id = item.document_id, item.id
        item.assigned_to = "usr_reviewer"
        session.commit()
    other = login(api, username="other-bulk-reviewer")
    url = f"/review/documents/{document_id}/confirm-remaining"
    assert api.client.post(url, headers=other).status_code == 409
    headers = login(api)
    import trade_extract.services.review as review_service

    original_resolve = review_service.resolve
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            from trade_extract.domain.errors import invalid_request

            raise invalid_request("field_path", "injected failure")
        return original_resolve(*args, **kwargs)

    monkeypatch.setattr(review_service, "resolve", fail_second)
    assert api.client.post(url, headers=headers).status_code == 400
    with api.session_factory() as session:
        assert list(session.scalars(select(ReviewResolution))) == []
        assert session.get(ReviewItem, item_id).status == "open"


def test_partial_extraction_correction_preserves_missing_fields(api, tmp_path, monkeypatch):
    invoices(api, tmp_path, monkeypatch)
    headers = login(api)
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        snapshot = deepcopy(document.extraction)
        del snapshot["fields"]["seller.name"]
        document.extraction = snapshot
        document_id = document.id
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.document_id == document.id))
        session.commit()
    field = deepcopy(snapshot["fields"]["invoice.number"])
    url = f"/review/queues/{item_id}/resolve"
    payload = {
        "outcome": "corrected",
        "field_path": "invoice.number",
        "reason": "Read retained partial source",
        "corrected_value": {**field, "value": "  "},
    }
    assert api.client.post(url, headers=headers, json=payload).status_code == 400
    payload["corrected_value"]["value"] = "PARTIAL-CORRECTED"
    response = api.client.post(url, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    with api.session_factory() as session:
        assert session.get(Document, document_id).extraction == snapshot
        label = session.scalar(select(ReviewResolution))
        assert label.corrected_value["value"] == "PARTIAL-CORRECTED"
        assert label.corrected_value["provenance"] == field["provenance"]
        assert "seller.name" not in session.get(Document, document_id).extraction["fields"]


@pytest.mark.parametrize("document_type", ["commercial_invoice", "proforma_invoice"])
def test_invoice_table_cells_are_reviewable_for_both_invoice_types(
    api, tmp_path, monkeypatch, document_type
):
    from tests.unit.test_invoice_gate_conflicts import invoice
    from trade_extract.domain.inference import ExtractionOutcome
    from trade_extract.schemas.registry import get_schema
    from trade_extract.workers.handlers import extract_segment

    raw = invoice(tmp_path).model_dump(mode="json")
    spec = get_schema(document_type)
    raw["document_type"] = document_type
    raw["schema_version"] = spec.version
    if document_type == "proforma_invoice":
        raw["fields"] = {
            path.replace("invoice.", "proforma.", 1) if path.startswith("invoice.") else path: value
            for path, value in raw["fields"].items()
            if path != "bill_of_lading.number"
        }

    def extract(provider, *, source, pages, segment_id, **kwargs):
        value = deepcopy(raw)
        value["source"] = source.model_dump(mode="json")
        value["segment_id"] = segment_id
        for field in value["fields"].values():
            field["provenance"]["document_hash"] = source.document_hash
            field["provenance"]["page_index"] = source.page_start
        return ExtractionOutcome("extracted", document=spec.model.model_validate(value))

    monkeypatch.setattr(extract_segment, "extract", extract)
    title = "PROFORMA INVOICE" if document_type == "proforma_invoice" else "COMMERCIAL INVOICE"
    response = post_files(api, {}, docs(make_pdf([[title, "Seller Buyer Goods Invoice Total 10"]])))
    assert response.status_code == 202, response.text
    accepted = response.json()
    drain(api)
    headers = login(api)
    url = f"/review/jobs/{accepted['job_id']}"
    page = api.client.get(url)
    assert page.status_code == 200, page.text
    assert 'class="line-items-table"' in page.text
    assert 'aria-label="Line item 1 Unit price"' in page.text
    assert 'name="field_path" value="line_items[0].unit_price"' in page.text
    assert 'title="line_items[0].unit_price">Line Items' not in page.text
    assert 'aria-label="Invoice document data"' in page.text
    assert 'data-tab-panel="items" hidden' in page.text
    items = api.client.get(f"{url}?tab=items")
    assert items.status_code == 200
    assert 'data-tab-panel="fields" hidden' in items.text
    assert 'data-tab-panel="items" hidden' not in items.text
    assert re.search(r'class="tab is-active"[^>]*tab=items', items.text)
    assert re.search(r'class="tab is-active"[^>]*tab=fields', page.text)
    assert "Reason for correction" not in page.text
    assert 'name="reason" value="Corrected during review."' in page.text
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        assert document.document_type == document_type
        original = deepcopy(document.extraction)
        item_id = session.scalar(select(ReviewItem.id).where(ReviewItem.document_id == document.id))
    corrected = {**deepcopy(original["fields"]["line_items[0].unit_price"]), "value": "6"}
    saved = api.client.post(
        f"/review/queues/{item_id}/resolve",
        headers=headers,
        json={
            "outcome": "corrected",
            "field_path": "line_items[0].unit_price",
            "reason": "Verified source unit price",
            "corrected_value": corrected,
        },
    )
    assert saved.status_code == 200, saved.text
    page = api.client.get(url)
    assert 'title="line_items[0].unit_price">6</span>' in page.text
    assert "Original value" in page.text


def test_original_source_document_streams_inline(api, tmp_path, monkeypatch):
    invoices(api, tmp_path, monkeypatch)
    login(api)
    with api.session_factory() as session:
        document_id = session.scalar(select(Document.id))
    response = api.client.get(f"/review/media/documents/{document_id}/source")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.content.startswith(b"%PDF")


def test_original_source_document_returns_404_for_unknown_document(api):
    login(api)
    response = api.client.get("/review/media/documents/doc_missing/source")
    assert response.status_code == 404


def test_create_job_cta_only_on_dashboard_and_jobs_list(api, tmp_path, monkeypatch):
    accepted = invoices(api, tmp_path, monkeypatch)
    login(api)
    cases = (
        ("/review", True),
        ("/review/jobs", True),
        (f"/review/jobs/{accepted['job_id']}", False),
        ("/review/queues/extraction", False),
        ("/review/activity", False),
        ("/review/team", False),
        ("/review/clients", False),
    )
    for path, expected in cases:
        page = api.client.get(path)
        assert page.status_code == 200, path
        assert ('href="/review/jobs/new"' in page.text) is expected, path


def test_job_detail_moves_back_nav_into_topbar(api, tmp_path, monkeypatch):
    accepted = invoices(api, tmp_path, monkeypatch)
    login(api)
    page = api.client.get(f"/review/jobs/{accepted['job_id']}")
    assert page.status_code == 200
    assert 'class="page-head"' not in page.text
    assert "<h1>Job details</h1>" not in page.text
    assert 'class="btn btn-secondary btn-sm topbar-back" href="/review/jobs"' in page.text
    assert 'class="icon-btn mobile-menu"' in page.text
