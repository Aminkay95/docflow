from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from trade_extract.config import Settings
from trade_extract.review.mail import MailDeliveryError, send_password_reset_email

RESET_URL = "https://app.example.com/review/reset-password?token=secret-token"


def mail_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        environment="development",
        database_url="postgresql+psycopg://trade:trade@localhost:5432/trade_extract",
        secret_key="unit-test-secret",
        api_key_pepper="unit-test-pepper",
        storage_dir=tmp_path / "storage",
        mail_outbox_dir=tmp_path / "outbox",
        base_url="http://testserver",
        resend_api_key="re_test_key",
        resend_from="Trade Extract <no-reply@example.com>",
    )
    values.update(overrides)
    return Settings(**values)


def test_resend_posts_the_reset_link(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "email_123"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        send_password_reset_email(
            mail_settings(tmp_path), "user@example.com", RESET_URL, http_client=client
        )

    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test_key"
    payload = json.loads(request.content)
    assert payload["from"] == "Trade Extract <no-reply@example.com>"
    assert payload["to"] == ["user@example.com"]
    assert RESET_URL in payload["text"]
    assert not (tmp_path / "outbox").exists()


def test_resend_takes_precedence_over_smtp(tmp_path: Path, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("SMTP must not be used when Resend is configured")

    monkeypatch.setattr("trade_extract.review.mail.smtplib.SMTP", fail)

    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "x"}))
    ) as client:
        send_password_reset_email(
            mail_settings(tmp_path, smtp_host="smtp.example.com"),
            "user@example.com",
            RESET_URL,
            http_client=client,
        )


def test_resend_failure_raises_without_leaking_the_link(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"name": "validation_error", "message": "bad from"})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(MailDeliveryError) as excinfo,
    ):
        send_password_reset_email(
            mail_settings(tmp_path), "user@example.com", RESET_URL, http_client=client
        )

    message = str(excinfo.value)
    assert "422" in message
    assert "validation_error" in message
    assert "secret-token" not in message


def test_outbox_is_used_when_no_provider_is_configured(tmp_path: Path) -> None:
    send_password_reset_email(
        mail_settings(tmp_path, resend_api_key=""),
        "user@example.com",
        RESET_URL,
    )

    files = sorted((tmp_path / "outbox").glob("*.eml"))
    assert len(files) == 1
    assert RESET_URL in files[0].read_text(encoding="utf-8")


def test_resend_requires_a_real_sender(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="RESEND_FROM"):
        mail_settings(tmp_path, resend_from="")


def test_production_requires_resend_or_smtp(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="RESEND_API_KEY or SMTP_HOST"):
        Settings(
            environment="production",
            secret_key="production-secret-value",
            api_key_pepper="production-pepper-value",
            storage_dir=tmp_path / "storage",
            resend_api_key="",
            smtp_host="",
        )
