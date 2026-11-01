from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import ApiHarness, count_rows, seed_client
from tests.support import make_pdf
from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.segment import Segment
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import tasks as tasks_repo
from trade_extract.domain.ids import new_id
from trade_extract.workers.runner import run_once

pytestmark = pytest.mark.integration


def post_files(api: ApiHarness, data: dict[str, str], files: list[tuple]):
    return api.client.post("/v1/extract", headers=api.headers, data=data, files=files)


def docs(*payloads: bytes) -> list[tuple]:
    return [
        ("documents", (f"document-{index}.pdf", payload, "application/pdf"))
        for index, payload in enumerate(payloads)
    ]


def drain(api: ApiHarness, *, max_loops: int = 50) -> int:
    for processed in range(max_loops):
        if not run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=api.app.state.clock,
            worker_id="test-worker",
        ):
            return processed
    raise AssertionError("worker did not drain")


def test_accept_poll_worker_complete(api: ApiHarness) -> None:
    accepted = post_files(api, {"job_ref": "BL-LIFECYCLE"}, docs(make_pdf([["INVOICE"]])))
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]

    queued = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert queued.headers["Cache-Control"] == "no-store"
    assert queued.headers["Retry-After"] == "2"
    assert queued.headers["ETag"]

    assert drain(api) == 2

    completed = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert completed.status_code == 200
    body = completed.json()
    assert body["status"] == "completed"
    assert body["documents"] == []
    assert body["conflicts"] == []
    assert body["decision"] is None
    assert body["completed_at"] is not None
    assert body["progress"]["documents_total"] == 1
    assert body["progress"]["documents_completed"] == 1
    assert body["policy"]["version"] == 1
    assert body["policy"]["effective_at"] is not None
    assert "Retry-After" not in completed.headers

    cached = api.client.get(
        f"/v1/results/{result_id}",
        headers={**api.headers, "If-None-Match": completed.headers["ETag"]},
    )
    assert cached.status_code == 304
    assert cached.content == b""

    list_cached = api.client.get(
        f"/v1/results/{result_id}",
        headers={
            **api.headers,
            "If-None-Match": f'"unrelated", W/{completed.headers["ETag"]}',
        },
    )
    assert list_cached.status_code == 304
    assert list_cached.content == b""


def test_replay_after_completion_returns_the_frozen_202_body(api: ApiHarness) -> None:
    files = docs(make_pdf([["INVOICE"]]))
    headers = {**api.headers, "Idempotency-Key": "frozen-replay-001"}
    first = api.client.post(
        "/v1/extract", headers=headers, data={"job_ref": "BL-FROZEN"}, files=files
    )
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    drain(api)

    completed = api.client.get(f"/v1/results/{first.json()['result_id']}", headers=api.headers)
    assert completed.json()["status"] == "completed"

    second = api.client.post(
        "/v1/extract", headers=headers, data={"job_ref": "BL-FROZEN"}, files=files
    )
    assert second.status_code == 202
    assert second.json() == first.json()
    assert second.json()["status"] == "queued"
    assert count_rows(api, "submissions") == 1
    assert count_rows(api, "files") == 1
    assert count_rows(api, "job_tasks") == 2


def test_unknown_include_value_is_rejected(api: ApiHarness) -> None:
    accepted = post_files(api, {"job_ref": "BL-INCLUDE"}, docs(make_pdf()))
    result_id = accepted.json()["result_id"]
    response = api.client.get(
        f"/v1/results/{result_id}?include=documents,nonsense", headers=api.headers
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"]["field"] == "include"
    assert "documents" in error["message"]


def test_document_id_is_accepted_for_an_unknown_id(api: ApiHarness) -> None:
    accepted = post_files(api, {"job_ref": "BL-DOC-FILTER"}, docs(make_pdf()))
    result_id = accepted.json()["result_id"]
    response = api.client.get(f"/v1/results/{result_id}?document_id=doc_nope", headers=api.headers)
    assert response.status_code == 200
    assert response.json()["documents"] == []


def test_completed_job_reopens_on_new_submission(api: ApiHarness) -> None:
    first = post_files(api, {"job_ref": "BL-REOPEN"}, docs(make_pdf([["INVOICE"]])))
    drain(api)
    job_id = first.json()["job_id"]

    with api.session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.terminal_at is not None

    second = post_files(api, {"job_ref": "BL-REOPEN"}, docs(make_pdf([["PACKING LIST"]])))
    assert second.status_code == 202
    assert second.json()["job_id"] == job_id
    assert second.json()["status"] == "queued"

    with api.session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == "queued"
        assert job.terminal_at is None
        assert job.terminal_reason is None

    drain(api)
    with api.session_factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.status == "completed"
    assert count_rows(api, "submissions") == 2
    assert count_rows(api, "files") == 2


def test_omitted_job_ref_never_accumulates(api: ApiHarness) -> None:
    payload = make_pdf()
    first = post_files(api, {}, docs(payload))
    second = post_files(api, {}, docs(payload))
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]
    assert second.json()["duplicate_documents"] == 0
    assert count_rows(api, "jobs") == 2


def test_unknown_result_is_404(api: ApiHarness) -> None:
    response = api.client.get("/v1/results/res_does_not_exist", headers=api.headers)
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "result_not_found"
    assert "No result exists" in error["message"]


def test_other_tenants_result_is_404(api: ApiHarness, second_tenant) -> None:
    other_token, _ = second_tenant
    other_headers = {"Authorization": f"Bearer {other_token}"}
    accepted = api.client.post(
        "/v1/extract",
        headers=other_headers,
        data={"job_ref": "BL-OTHER"},
        files=docs(make_pdf()),
    )
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]

    response = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "result_not_found"


def test_result_read_requires_scope(api: ApiHarness) -> None:
    token, _, _ = seed_client(api.session_factory, api.settings, scopes=["extract:write"])
    response = api.client.get(
        "/v1/results/res_anything", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_scope"] == "results:read"


def test_document_idempotency_is_enforced_by_schema(api: ApiHarness) -> None:
    accepted = post_files(api, {"job_ref": "BL-SCHEMA"}, docs(make_pdf()))
    assert accepted.status_code == 202
    drain(api)

    with api.session_factory() as session:
        job = session.execute(select(Job)).scalars().one()
        submission = session.execute(select(Submission)).scalars().one()
        file_row = session.execute(select(File)).scalars().one()

        def add_document(page: int, document_hash: str, key: str) -> None:
            segment = Segment(
                id=new_id("seg_"),
                file_id=file_row.id,
                job_id=job.id,
                page_start=page,
                page_end=page,
                document_type="unknown",
                confidence=0.5,
                fingerprint=f"fp-{page}",
                status="pending",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(segment)
            session.flush()
            session.add(
                Document(
                    id=new_id("doc_"),
                    job_id=job.id,
                    submission_id=submission.id,
                    file_id=file_row.id,
                    segment_id=segment.id,
                    client_id=job.client_id,
                    document_type="unknown",
                    source_file_hash=file_row.sha256,
                    document_hash=document_hash,
                    idempotency_key=key,
                    status="queued",
                    page_start=page,
                    page_end=page,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.flush()

        add_document(0, "hash-a", "same-key")
        with pytest.raises(IntegrityError):
            add_document(1, "hash-b", "same-key")
        session.rollback()


def test_job_document_count_is_not_fabricated(api: ApiHarness) -> None:
    accepted = post_files(api, {"job_ref": "BL-COUNT"}, docs(make_pdf()))
    assert accepted.status_code == 202
    drain(api)
    with api.session_factory() as session:
        count = session.execute(text("SELECT document_count FROM jobs")).scalar_one()
    assert count == 0


def test_finalize_rearms_while_siblings_are_active(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        {"job_ref": "BL-REARM"},
        docs(make_pdf([["ONE"]]), make_pdf([["TWO"]])),
    )
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]

    with api.session_factory() as session:
        submission = session.execute(select(Submission)).scalars().one()
        tasks_repo.enqueue(
            session,
            job_id=submission.job_id,
            submission_id=submission.id,
            kind="finalize_submission",
            payload={"submission_id": submission.id, "job_id": submission.job_id},
            dedupe_key=f"finalize_submission:{submission.id}",
            priority=1000,
            now=api.app.state.clock.now(),
        )
        session.commit()

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="rearm-worker",
    )

    with api.session_factory() as session:
        finalize = (
            session.execute(select(JobTask).where(JobTask.kind == "finalize_submission"))
            .scalars()
            .one()
        )
        assert finalize.status == "queued"
        assert finalize.attempts == 0

    drain(api)
    completed = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert count_rows(api, "job_tasks") == 3


def test_failed_file_is_reprocessed_rather_than_counted_as_a_duplicate(
    api: ApiHarness,
) -> None:
    payload = make_pdf([["INVOICE"]])
    first = post_files(api, {"job_ref": "BL-RETRY-FILE"}, docs(payload))
    assert first.status_code == 202
    drain(api)

    with api.session_factory() as session:
        session.execute(
            text(
                "UPDATE files SET status = 'failed', error_code = 'boom', "
                "error_message = 'processing exploded'"
            )
        )
        session.execute(
            text(
                "UPDATE jobs SET status = 'processing', terminal_at = NULL, terminal_reason = NULL"
            )
        )
        session.commit()

    second = post_files(api, {"job_ref": "BL-RETRY-FILE"}, docs(payload))
    assert second.status_code == 202, second.text
    assert second.json()["duplicate_documents"] == 0
    assert second.json()["status"] == "queued"

    with api.session_factory() as session:
        assert count_rows(api, "files") == 1
        assert count_rows(api, "job_tasks") == 2
        file_status = session.execute(text("SELECT status FROM files")).scalar_one()
        assert file_status == "received"

    drain(api)
    assert count_rows(api, "job_tasks") == 3
    completed = api.client.get(f"/v1/results/{second.json()['result_id']}", headers=api.headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
