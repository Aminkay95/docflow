from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration.conftest import ApiHarness, count_rows, seed_client
from tests.support import DOCX_BYTES, make_encrypted_pdf, make_pdf, temp_snapshot

pytestmark = pytest.mark.integration


def storage_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def post_files(
    api: ApiHarness,
    data: dict[str, str],
    files: list[tuple],
    *,
    headers: dict[str, str] | None = None,
):
    return api.client.post(
        "/v1/extract",
        headers=headers or api.headers,
        data=data,
        files=files,
    )


def docs(*payloads: bytes) -> list[tuple]:
    return [
        ("documents", (f"document-{index}.pdf", payload, "application/pdf"))
        for index, payload in enumerate(payloads)
    ]


def assert_nothing_persisted(api: ApiHarness) -> None:
    assert count_rows(api, "jobs") == 0
    assert count_rows(api, "submissions") == 0
    assert count_rows(api, "files") == 0
    assert count_rows(api, "job_tasks") == 0
    assert storage_files(api.storage_dir) == []


def test_happy_path_accepts_a_group(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        docs(make_pdf([["INVOICE"]]), make_pdf([["PACKING LIST"]]), make_pdf([["BILL OF LADING"]])),
        headers={**api.headers, "Idempotency-Key": "key-happy-path-1"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_ref"] == "BL-1"
    assert body["submitted_documents"] == 3
    assert body["duplicate_documents"] == 0
    assert body["result_id"].startswith("res_")
    assert body["job_id"].startswith("job_")
    assert body["status_url"].endswith(body["result_id"])
    assert body["job_url"].endswith(f"/v1/jobs/{body['job_id']}")
    assert response.headers["Location"] == f"/v1/results/{body['result_id']}"
    assert response.headers["Retry-After"] == "2"

    assert count_rows(api, "jobs") == 1
    assert count_rows(api, "submissions") == 1
    assert count_rows(api, "files") == 3
    assert count_rows(api, "job_tasks") == 3
    assert len(storage_files(api.storage_dir)) == 3


def test_zero_files_rejected(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        [("other", ("note.txt", b"not a document", "text/plain"))],
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_documents"
    assert "1 and at most 20" in error["message"]
    assert error["details"]["limit_min"] == 1
    assert_nothing_persisted(api)


def test_group_over_twenty_rejected_before_storage(api: ApiHarness) -> None:
    response = post_files(api, {"job_ref": "BL-1"}, docs(*([make_pdf()] * 21)))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "too_many_documents"
    assert error["details"] == {"limit": 20, "actual": 21}
    assert "Split it into groups of 20" in error["message"]
    assert_nothing_persisted(api)


def test_zero_byte_part_rejected_before_storage(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        [("documents", ("empty.pdf", b"", "application/pdf"))],
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "malformed_document"
    assert "empty" in error["message"]
    assert_nothing_persisted(api)


def test_whole_request_cap_rejected_before_storage(api: ApiHarness) -> None:
    api.settings.max_request_bytes = 500
    response = post_files(api, {"job_ref": "BL-1"}, docs(make_pdf()))
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["details"]["limit_bytes"] == 500
    assert error["details"]["actual_bytes"] > 500
    assert_nothing_persisted(api)


def test_file_too_large_rejected_mid_stream(api: ApiHarness) -> None:
    api.settings.max_file_bytes = 100
    response = post_files(api, {"job_ref": "BL-1"}, docs(make_pdf()))
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "file_too_large"
    assert error["details"]["limit_bytes"] == 100
    assert error["details"]["actual_bytes"] > 100
    assert "Compress or downscale" in error["message"]
    assert_nothing_persisted(api)


def test_unsupported_magic_bytes_rejected(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        [("documents", ("invoice.pdf", DOCX_BYTES, "application/pdf"))],
    )
    assert response.status_code == 415
    error = response.json()["error"]
    assert error["code"] == "unsupported_media_type"
    assert "application/pdf" in error["message"]
    assert_nothing_persisted(api)


def test_encrypted_pdf_rejected(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        [("documents", ("locked.pdf", make_encrypted_pdf(), "application/pdf"))],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "encrypted_document"
    assert_nothing_persisted(api)


def test_malformed_pdf_rejected(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        [("documents", ("broken.pdf", b"%PDF-1.4\nnot a pdf", "application/pdf"))],
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "malformed_document"
    assert_nothing_persisted(api)


def test_page_limit_rejected(api: ApiHarness) -> None:
    api.settings.max_pages_per_file = 1
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        [("documents", ("two.pdf", make_pdf([["ONE"], ["TWO"]]), "application/pdf"))],
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "page_limit_exceeded"
    assert error["details"]["file_pages"] == 2
    assert error["details"]["limits"]["max_pages_per_file"] == 1
    assert_nothing_persisted(api)


def test_multi_shipment_rejected(api: ApiHarness) -> None:
    payload = make_pdf(
        [
            [
                "BILL OF LADING",
                "B/L No: KEMU123456789",
                "COMMERCIAL INVOICE",
                "B/L KEMU987654321",
            ]
        ]
    )
    response = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "multi_shipment_container"
    assert error["details"]["shipment_count"] == 2
    assert error["details"]["bill_of_lading_numbers"] == [
        "KEMU123456789",
        "KEMU987654321",
    ]
    assert "one shipment per job_ref" in error["message"]
    assert_nothing_persisted(api)


def test_one_bill_of_lading_accepted(api: ApiHarness) -> None:
    payload = make_pdf([["BILL OF LADING", "B/L No: KEMU123456789"], ["INVOICE"]])
    response = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert response.status_code == 202, response.text


def test_extra_tokens_on_a_bill_of_lading_line_are_not_evidence(api: ApiHarness) -> None:
    payload = make_pdf(
        [
            [
                "BILL OF LADING",
                "B/L No: KEMU123456789 DATE 2026-10-31 SEAL S-998877 INVOICE INV-424242",
                "CONTAINER MSKU1234567",
            ],
            ["COMMERCIAL INVOICE", "B/L KEMU123456789"],
        ]
    )
    response = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert response.status_code == 202, response.text


def test_idempotent_replay_returns_original_body(api: ApiHarness) -> None:
    files = docs(make_pdf())
    headers = {**api.headers, "Idempotency-Key": "replay-key-0001"}
    first = api.client.post("/v1/extract", headers=headers, data={"job_ref": "BL-1"}, files=files)
    assert first.status_code == 202
    second = api.client.post("/v1/extract", headers=headers, data={"job_ref": "BL-1"}, files=files)
    assert second.status_code == 202
    assert second.json() == first.json()
    assert count_rows(api, "submissions") == 1
    assert count_rows(api, "files") == 1
    assert count_rows(api, "job_tasks") == 1


def test_idempotency_key_reuse_with_different_body(api: ApiHarness) -> None:
    headers = {**api.headers, "Idempotency-Key": "replay-key-0002"}
    first = api.client.post(
        "/v1/extract", headers=headers, data={"job_ref": "BL-1"}, files=docs(make_pdf())
    )
    assert first.status_code == 202
    second = api.client.post(
        "/v1/extract",
        headers=headers,
        data={"job_ref": "BL-1"},
        files=docs(make_pdf([["DIFFERENT"]])),
    )
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "idempotency_key_reused"
    assert "new Idempotency-Key" in error["message"]
    assert count_rows(api, "submissions") == 1
    assert count_rows(api, "files") == 1


def test_duplicate_document_not_reprocessed(api: ApiHarness) -> None:
    payload = make_pdf()
    first = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert first.status_code == 202
    second = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert second.status_code == 202
    body = second.json()
    assert body["submitted_documents"] == 1
    assert body["duplicate_documents"] == 1
    assert body["job_id"] == first.json()["job_id"]
    assert count_rows(api, "files") == 1
    assert count_rows(api, "job_tasks") == 1
    assert count_rows(api, "submissions") == 2


def test_all_duplicates_complete_immediately(api: ApiHarness) -> None:
    payload = make_pdf()
    post_files(api, {"job_ref": "BL-1"}, docs(payload))
    second = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert second.status_code == 202
    body = second.json()
    assert body["status"] == "queued"
    assert body["duplicate_documents"] == 1
    assert count_rows(api, "job_tasks") == 1

    with api.session_factory() as session:
        submission = (
            session.execute(
                text(
                    "SELECT status, completed_at FROM submissions ORDER BY created_at DESC LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
    assert submission["status"] == "completed"
    assert submission["completed_at"] is not None


def test_duplicate_within_one_submission_counts_once(api: ApiHarness) -> None:
    payload = make_pdf()
    response = post_files(api, {"job_ref": "BL-1"}, docs(payload, payload))
    assert response.status_code == 202
    body = response.json()
    assert body["submitted_documents"] == 2
    assert body["duplicate_documents"] == 1
    assert count_rows(api, "files") == 1
    assert count_rows(api, "job_tasks") == 1


def test_failed_job_refuses_new_documents(api: ApiHarness) -> None:
    post_files(api, {"job_ref": "BL-FAILED"}, docs(make_pdf()))
    with api.session_factory() as session:
        session.execute(
            text(
                "UPDATE jobs SET status = 'failed', terminal_at = now(), terminal_reason = 'failed'"
            )
        )
        session.commit()

    response = post_files(api, {"job_ref": "BL-FAILED"}, docs(make_pdf([["OTHER"]])))
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "job_failed"
    assert "new job_ref" in error["message"]
    assert count_rows(api, "submissions") == 1
    assert count_rows(api, "files") == 1


def test_bad_webhook_url_rejected(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1", "webhook_url": "not a url"},
        docs(make_pdf()),
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"]["field"] == "webhook_url"
    assert_nothing_persisted(api)


def test_webhook_url_requires_https_in_production(api: ApiHarness) -> None:
    api.settings.environment = "production"
    response = post_files(
        api,
        {"job_ref": "BL-1", "webhook_url": "http://hooks.example.test/ingest"},
        docs(make_pdf()),
    )
    assert response.status_code == 400
    assert "HTTPS" in response.json()["error"]["message"]
    assert_nothing_persisted(api)


def test_empty_webhook_url_is_treated_as_absent(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-EMPTY-HOOK", "webhook_url": ""},
        docs(make_pdf()),
    )
    assert response.status_code == 202, response.text
    with api.session_factory() as session:
        stored = session.execute(text("SELECT webhook_url FROM submissions")).scalar_one()
    assert stored is None


def test_auth_missing_key(api: ApiHarness) -> None:
    response = api.client.post("/v1/extract", files=docs(make_pdf()))
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert error["request_id"]
    assert error["doc_url"].endswith("#unauthorized")


def test_auth_revoked_key(api: ApiHarness) -> None:
    with api.session_factory() as session:
        session.execute(text("UPDATE api_keys SET revoked_at = now()"))
        session.commit()
    response = post_files(api, {"job_ref": "BL-1"}, docs(make_pdf()))
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert "revoked" in error["message"].lower()
    assert "not recognised" not in error["message"].lower()


def test_auth_missing_scope(api: ApiHarness) -> None:
    token, _, _ = seed_client(api.session_factory, api.settings, scopes=["results:read"])
    response = api.client.post(
        "/v1/extract",
        headers={"Authorization": f"Bearer {token}"},
        data={"job_ref": "BL-1"},
        files=docs(make_pdf()),
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "forbidden"
    assert error["details"]["required_scope"] == "extract:write"


def test_rejections_leave_no_temp_files(api: ApiHarness, temp_upload_snapshot) -> None:
    before = temp_upload_snapshot
    payload = make_pdf([["BILL OF LADING", "B/L KEMU123456789", "B/L KEMU987654321"]])
    response = post_files(api, {"job_ref": "BL-1"}, docs(payload))
    assert response.status_code == 422
    after = temp_snapshot()
    assert after <= before
    assert_nothing_persisted(api)


def test_idempotency_insert_race_replays_instead_of_500(api: ApiHarness, monkeypatch) -> None:
    from trade_extract.db.repositories import submissions as submissions_repo
    from trade_extract.services import ingest

    files = docs(make_pdf())
    headers = {**api.headers, "Idempotency-Key": "race-key-0001"}
    first = api.client.post(
        "/v1/extract", headers=headers, data={"job_ref": "BL-RACE"}, files=files
    )
    assert first.status_code == 202

    real_lookup = submissions_repo.get_by_idempotency_key
    calls = {"count": 0}

    def miss_until_insert(session, client_id, idempotency_key):
        calls["count"] += 1
        if calls["count"] <= 2:
            return None
        return real_lookup(session, client_id, idempotency_key)

    monkeypatch.setattr(ingest.submissions_repo, "get_by_idempotency_key", miss_until_insert)

    second = api.client.post(
        "/v1/extract", headers=headers, data={"job_ref": "BL-RACE"}, files=files
    )
    assert second.status_code == 202, second.text
    assert second.json() == first.json()
    assert calls["count"] >= 3
    assert count_rows(api, "submissions") == 1
    assert count_rows(api, "files") == 1
    assert count_rows(api, "job_tasks") == 1


def test_job_ref_insert_race_retries_the_upsert_instead_of_500(
    api: ApiHarness, monkeypatch
) -> None:
    from trade_extract.db.repositories import jobs as jobs_repo
    from trade_extract.services import ingest

    real_create = jobs_repo.create
    raced = {"done": False}

    def create_with_competitor(session, **kwargs):
        if not raced["done"]:
            raced["done"] = True
            # Another request wins the (client, job_ref) insert between our lookup and ours.
            with api.session_factory() as other:
                real_create(other, **kwargs)
                other.commit()
        return real_create(session, **kwargs)

    monkeypatch.setattr(ingest.jobs_repo, "create", create_with_competitor)

    response = post_files(api, {"job_ref": "BL-JOB-RACE"}, docs(make_pdf([["RACE"]])))

    assert response.status_code == 202, response.text
    assert raced["done"] is True
    assert count_rows(api, "jobs") == 1
    assert count_rows(api, "submissions") == 1


def test_all_duplicates_completion_enqueues_the_webhook(api: ApiHarness) -> None:
    payload = make_pdf([["INVOICE"]])
    first = post_files(
        api,
        {"job_ref": "BL-DUP-HOOK", "webhook_url": "https://hooks.example.test/hook"},
        docs(payload),
    )
    assert first.status_code == 202

    second = post_files(
        api,
        {"job_ref": "BL-DUP-HOOK", "webhook_url": "https://hooks.example.test/hook"},
        docs(payload),
    )
    assert second.status_code == 202
    assert second.json()["duplicate_documents"] == 1

    with api.session_factory() as session:
        event = (
            session.execute(text("SELECT event_type, status FROM webhook_deliveries"))
            .mappings()
            .all()
        )
    assert [(row["event_type"], row["status"]) for row in event] == [
        ("result.completed", "pending")
    ]


def test_job_ref_with_a_trailing_newline_is_rejected(api: ApiHarness) -> None:
    response = post_files(api, {"job_ref": "BL-1\n"}, docs(make_pdf()))
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["details"]["field"] == "job_ref"
    assert_nothing_persisted(api)


def test_short_idempotency_key_is_rejected(api: ApiHarness) -> None:
    response = post_files(
        api,
        {"job_ref": "BL-1"},
        docs(make_pdf()),
        headers={**api.headers, "Idempotency-Key": "short"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["details"]["field"] == "Idempotency-Key"
    assert_nothing_persisted(api)


def test_group_rejected_by_a_later_file_stores_nothing(api: ApiHarness) -> None:
    api.settings.max_pages_per_file = 1
    response = post_files(
        api,
        {"job_ref": "BL-LATE"},
        docs(make_pdf([["ONE"]]), make_pdf([["ONE"], ["TWO"]])),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "page_limit_exceeded"
    assert_nothing_persisted(api)


def test_successful_submission_leaves_no_temp_files(api: ApiHarness, temp_upload_snapshot) -> None:
    before = temp_upload_snapshot
    response = post_files(api, {"job_ref": "BL-TEMP"}, docs(make_pdf()))
    assert response.status_code == 202
    assert temp_snapshot() <= before


def test_unhandled_exception_returns_the_error_envelope(api: ApiHarness, monkeypatch) -> None:
    from starlette.testclient import TestClient

    accepted = post_files(api, {"job_ref": "BL-500"}, docs(make_pdf()))
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]

    def boom(*args, **kwargs):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr("trade_extract.api.routes.results.submissions_repo.get_by_result_id", boom)

    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.get(f"/v1/results/{result_id}", headers=api.headers)

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert response.headers["X-Request-Id"] == error["request_id"]
    assert "storage exploded" not in response.text
