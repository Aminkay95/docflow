"""Password reset delivery: Resend, then SMTP, then a local file outbox."""

from __future__ import annotations

import logging
import secrets
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx

from trade_extract.config import Settings

logger = logging.getLogger(__name__)

_SUBJECT = "Reset your Trade Extract password"


class MailDeliveryError(RuntimeError):
    """The configured email provider rejected the message."""


def send_password_reset_email(
    settings: Settings,
    recipient: str,
    reset_url: str,
    *,
    http_client: httpx.Client | None = None,
) -> None:
    """Deliver the reset link through Resend, SMTP, or the development outbox.

    Resend wins when ``RESEND_API_KEY`` is set, then SMTP when ``SMTP_HOST`` is
    set, then the outbox. The outbox keeps local development working without a
    mail provider and is never used in production (the settings guard rejects
    production without Resend or SMTP).
    """

    body = (
        "A password reset was requested for your Trade Extract review account.\n\n"
        "Open this link to choose a new password:\n"
        f"{reset_url}\n\n"
        f"The link expires in {settings.password_reset_ttl_minutes} minutes and can be used once.\n"
        "If you did not request this, you can ignore this message.\n"
    )

    if settings.resend_api_key.get_secret_value().strip():
        send_with_resend(settings, recipient, body, http_client=http_client)
        logger.info("password reset email sent via Resend")
        return

    if settings.smtp_host:
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = recipient
        message["Subject"] = _SUBJECT
        message.set_content(body)
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
        ) as smtp:
            smtp.ehlo()
            if settings.smtp_starttls:
                smtp.starttls()
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(message)
        logger.info("password reset email sent via SMTP")
        return

    outbox = settings.mail_outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = outbox / f"{stamp}-{secrets.token_hex(4)}.eml"
    path.write_text(f"To: {recipient}\nSubject: {_SUBJECT}\n\n{body}", encoding="utf-8")
    logger.info("password reset email written to outbox", extra={"path": str(path)})


def send_with_resend(
    settings: Settings,
    recipient: str,
    body: str,
    *,
    http_client: httpx.Client | None = None,
) -> None:
    """POST the message to the Resend email API."""

    payload = {
        "from": settings.mail_from,
        "to": [recipient],
        "subject": _SUBJECT,
        "text": body,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(
        connect=settings.resend_timeout_seconds,
        read=settings.resend_timeout_seconds,
        write=settings.resend_timeout_seconds,
        pool=settings.resend_timeout_seconds,
    )
    if http_client is None:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(settings.resend_api_url, json=payload, headers=headers)
    else:
        response = http_client.post(settings.resend_api_url, json=payload, headers=headers)

    if not 200 <= response.status_code < 300:
        detail = _error_name(response)
        raise MailDeliveryError(
            f"Resend rejected the message: HTTP {response.status_code} {detail}"
        )


def _error_name(response: httpx.Response) -> str:
    """Return Resend's error name without echoing the request or recipient."""

    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        name = payload.get("name") or payload.get("message")
        if isinstance(name, str):
            return name[:120]
    return ""
