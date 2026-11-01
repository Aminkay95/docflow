from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trade_extract.api.app import create_app
from trade_extract.api.errors import error_response_payload
from trade_extract.config import Settings
from trade_extract.domain import errors

ENVELOPE_KEYS = {"code", "message", "request_id", "details", "doc_url"}

pytestmark = pytest.mark.contract


def all_errors() -> list:
    return [
        errors.invalid_request("job_ref", "must not be empty"),
        errors.unauthorized(),
        errors.forbidden("extract:write"),
        errors.result_not_found(),
        errors.job_not_found(),
        errors.document_not_found(),
        errors.idempotency_key_reused(),
        errors.job_failed(),
        errors.too_many_documents(20, 21),
        errors.no_documents(20),
        errors.file_too_large("invoice.pdf", 26214400, 32715571),
        errors.unsupported_media_type("invoice.docx", ["application/pdf"]),
        errors.encrypted_document("locked.pdf"),
        errors.malformed_document("broken.pdf"),
        errors.page_limit_exceeded(
            "big.pdf",
            file_pages=501,
            submission_pages=501,
            max_pages_per_file=500,
            max_pages_per_submission=2000,
        ),
        errors.multi_shipment_container(["KEMU123456789", "KEMU987654321"]),
        errors.invalid_webhook_url("must use HTTPS in production", value="https://x@y/z"),
        errors.payload_too_large(1000, 1200),
        errors.client_inactive("suspended"),
        errors.service_unavailable("database unavailable"),
        errors.internal_error(),
    ]


def test_every_error_uses_the_single_envelope() -> None:
    for error in all_errors():
        payload = error_response_payload(error, "req_test")
        assert set(payload) == {"error"}
        assert set(payload["error"]) == ENVELOPE_KEYS
        assert payload["error"]["code"] == error.code
        assert payload["error"]["message"].strip()
        assert payload["error"]["message"].endswith(".")
        assert payload["error"]["request_id"] == "req_test"
        assert payload["error"]["doc_url"].endswith(f"#{error.code}")
        assert isinstance(payload["error"]["details"], dict)


def test_messages_name_rule_and_remediation() -> None:
    assert "21 files" in errors.too_many_documents(20, 21).message
    assert "Split it" in errors.too_many_documents(20, 21).message
    assert "Compress or downscale" in errors.file_too_large("a.pdf", 10, 20).message
    assert (
        "one shipment per job_ref"
        in errors.multi_shipment_container(["A123456", "B123456"]).message
    )
    assert "new job_ref" in errors.job_failed().message
    assert "new Idempotency-Key" in errors.idempotency_key_reused().message
    assert "Remove the password" in errors.encrypted_document("a.pdf").message
    assert "Re-export" in errors.malformed_document("a.pdf").message
    assert "Split the group" in errors.payload_too_large(10, 20).message
    assert "suspended" in errors.client_inactive("suspended").message


def app_client(tmp_path: Path) -> TestClient:
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://trade:trade@localhost:5432/unused",
            secret_key="contract-secret",
            api_key_pepper="contract-pepper",
            storage_dir=tmp_path / "storage",
        )
    )
    return TestClient(app)


def test_missing_auth_returns_the_envelope(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        response = client.post("/v1/extract")
    assert response.status_code == 401
    error = response.json()["error"]
    assert set(error) == ENVELOPE_KEYS
    assert error["code"] == "unauthorized"
    assert response.headers["X-Request-Id"] == error["request_id"]


def test_request_id_is_echoed_when_valid(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        response = client.get("/v1/health", headers={"X-Request-Id": "client-req-0001"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "client-req-0001"


def test_unknown_path_uses_the_envelope(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        response = client.get("/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "endpoint_not_found"


def test_wrong_method_uses_the_envelope(tmp_path: Path) -> None:
    with app_client(tmp_path) as client:
        response = client.delete("/v1/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "invalid_request"
