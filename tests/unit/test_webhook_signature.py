from __future__ import annotations

import socket
from datetime import UTC, datetime

import pytest

from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.domain import errors
from trade_extract.services.webhooks import (
    WebhookTargetUnavailable,
    build_payload,
    decrypt_webhook_secret,
    encrypt_webhook_secret,
    ensure_safe_target,
    signature_for,
)


def test_signature_matches_documented_vector() -> None:
    body = b'{"a":1}'
    assert (
        signature_for("whsec_test", 1700000000, body)
        == "v1=38877139021993b830af32feea6e18a8da83eb2f6e49ee50bd9e4cf4ca4d3789"
    )


def test_webhook_secret_round_trip() -> None:
    ciphertext = encrypt_webhook_secret("whsec_abc", "process-secret")
    assert ciphertext != b"whsec_abc"
    assert decrypt_webhook_secret(ciphertext, "process-secret") == "whsec_abc"


def test_webhook_payload_carries_ids_counts_and_url_only(unit_settings) -> None:
    now = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    submission = Submission(
        id="sub_1",
        result_id="res_1",
        job_id="job_1",
        client_id="cli_1",
        status="completed",
        document_count=3,
        duplicate_count=0,
        created_at=now,
    )
    job = Job(
        id="job_1",
        client_id="cli_1",
        job_ref="BL-1",
        status="completed",
        policy_id="pol_1",
        document_count=0,
        open_review_count=0,
        open_conflict_count=0,
        created_at=now,
        updated_at=now,
        last_submission_at=now,
    )
    payload = build_payload(
        submission=submission,
        job=job,
        event_type="result.completed",
        settings=unit_settings,
        now=now,
    )
    assert payload["type"] == "result.completed"
    assert payload["data"]["result_id"] == "res_1"
    assert payload["data"]["job_id"] == "job_1"
    assert payload["data"]["document_count"] == 3
    assert payload["data"]["result_url"].endswith("/v1/results/res_1")
    assert "documents" not in payload["data"]
    assert "content" not in str(payload).lower()


def test_ssrf_guard_blocks_loopback_and_metadata(unit_settings) -> None:
    unit_settings = unit_settings.model_copy(update={"webhook_allow_private_targets": False})
    with pytest.raises(errors.DomainError) as caught:
        ensure_safe_target("http://127.0.0.1:9000/hook", unit_settings)
    assert caught.value.code == "invalid_request"

    with pytest.raises(errors.DomainError):
        ensure_safe_target("http://169.254.169.254/latest/meta-data", unit_settings)


def test_ssrf_guard_can_be_relaxed_for_tests(unit_settings) -> None:
    relaxed = unit_settings.model_copy(update={"webhook_allow_private_targets": True})
    ensure_safe_target("http://127.0.0.1:9000/hook", relaxed)


def test_ssrf_guard_rejects_a_non_numeric_port(unit_settings) -> None:
    relaxed = unit_settings.model_copy(update={"webhook_allow_private_targets": False})
    with pytest.raises(errors.DomainError) as caught:
        ensure_safe_target("https://hooks.example.test:notaport/hook", relaxed)
    assert caught.value.code == "invalid_request"
    assert "port" in caught.value.message


def test_transient_dns_failure_is_retryable(unit_settings, monkeypatch) -> None:
    relaxed = unit_settings.model_copy(update={"webhook_allow_private_targets": False})

    def temporary_failure(*args, **kwargs):
        raise socket.gaierror(socket.EAI_AGAIN, "temporary failure in name resolution")

    monkeypatch.setattr("trade_extract.services.webhooks.socket.getaddrinfo", temporary_failure)
    with pytest.raises(WebhookTargetUnavailable):
        ensure_safe_target("https://hooks.example.test/hook", relaxed)


def test_permanently_unknown_host_is_an_invalid_url(unit_settings, monkeypatch) -> None:
    relaxed = unit_settings.model_copy(update={"webhook_allow_private_targets": False})

    def unknown_host(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "name does not resolve")

    monkeypatch.setattr("trade_extract.services.webhooks.socket.getaddrinfo", unknown_host)
    with pytest.raises(errors.DomainError) as caught:
        ensure_safe_target("https://hooks.example.test/hook", relaxed)
    assert caught.value.code == "invalid_request"
