from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.webhook import WebhookDelivery
from trade_extract.db.repositories import webhooks as webhook_repo
from trade_extract.domain import errors
from trade_extract.domain.clock import Clock
from trade_extract.domain.hashing import sha256_bytes, stable_json
from trade_extract.domain.ids import new_id

RETRY_SCHEDULE_SECONDS = (60, 300, 1800, 7200, 21600, 43200, 86400, 86400)


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def _fernet(secret_key: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_webhook_secret(secret: str, secret_key: str) -> bytes:
    return _fernet(secret_key).encrypt(secret.encode("utf-8"))


def decrypt_webhook_secret(ciphertext: bytes, secret_key: str) -> str:
    try:
        return _fernet(secret_key).decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise errors.service_unavailable("webhook secret cannot be decrypted") from exc


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def signature_for(secret: str, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"v1={digest}"


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def _is_blocked_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    # An IPv4-mapped IPv6 address reaches the IPv4 host, so judge the v4 form.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # Cloud metadata endpoints live in link-local space, but be explicit.
    return ip in {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("100.100.100.200"),
    }


class WebhookTargetUnavailable(Exception):
    """A target lookup failed transiently; the delivery stays retryable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_TRANSIENT_GAI_ERRORS = {
    getattr(socket, "EAI_AGAIN", None),
    getattr(socket, "EAI_FAIL", None),
    getattr(socket, "EAI_SYSTEM", None),
}


def ensure_safe_target(url: str, settings: Settings) -> None:
    """Resolve a webhook target and refuse private, loopback and metadata addresses."""

    remedy: dict[str, Any] = {
        "max_length": settings.max_webhook_url_length,
        "require_https": settings.require_https_webhooks,
    }
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise errors.invalid_webhook_url(
            "must be a well-formed absolute URL with a numeric port",
            value=url,
            **remedy,
        ) from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise errors.invalid_webhook_url("must be an absolute http(s) URL", value=url, **remedy)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise errors.invalid_webhook_url(
            "must not embed credentials in the URL", value=url, **remedy
        )
    if settings.require_https_webhooks and parsed.scheme != "https":
        raise errors.invalid_webhook_url("must use HTTPS outside development", value=url, **remedy)
    if settings.webhook_allow_private_targets:
        return
    try:
        addresses = {
            str(info[4][0])
            for info in socket.getaddrinfo(
                parsed.hostname, port or (443 if parsed.scheme == "https" else 80)
            )
        }
    except socket.gaierror as exc:
        if exc.errno in _TRANSIENT_GAI_ERRORS:
            raise WebhookTargetUnavailable("host resolution temporarily failed") from exc
        raise errors.invalid_webhook_url("host cannot be resolved", value=url, **remedy) from exc
    if not addresses:
        raise WebhookTargetUnavailable("host resolution returned no addresses")
    for address in addresses:
        if _is_blocked_address(address):
            raise errors.invalid_webhook_url(
                "must not target a private, loopback or metadata address",
                value=url,
                **remedy,
            )


# ---------------------------------------------------------------------------
# Payload and outbox
# ---------------------------------------------------------------------------


def result_url(settings: Settings, result_id: str) -> str:
    return f"{settings.base_url}/v1/results/{result_id}"


def build_payload(
    *,
    submission: Submission,
    job: Job,
    event_type: str,
    settings: Settings,
    now: datetime,
    conflict_count: int = 0,
) -> dict[str, Any]:
    """Ids, counts and a URL only. Document content never enters a webhook payload."""

    return {
        "id": new_id("evt_"),
        "type": event_type,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "data": {
            "result_id": submission.result_id,
            "job_id": job.id,
            "job_ref": job.job_ref,
            "status": submission.status,
            "document_count": submission.document_count,
            "conflict_count": conflict_count,
            "review": {
                "extraction_open": job.open_review_count,
                "conflict_open": job.open_conflict_count,
            },
            "result_url": result_url(settings, submission.result_id),
        },
    }


def enqueue_completion(
    session: Session,
    *,
    submission: Submission,
    job: Job,
    settings: Settings,
    now: datetime,
    event_type: str = "result.completed",
) -> WebhookDelivery | None:
    url = submission.webhook_url
    if not url:
        client = session.get(Client, submission.client_id)
        if client is not None and client.webhook_enabled and client.webhook_url:
            url = client.webhook_url
    if not url:
        return None
    payload = build_payload(
        submission=submission,
        job=job,
        event_type=event_type,
        settings=settings,
        now=now,
        conflict_count=job.open_conflict_count,
    )
    from trade_extract.api.schemas.results import Result
    from trade_extract.services.ingest import build_result

    session.flush()
    payload["data"]["result"] = Result.model_validate(build_result(session, submission)).model_dump(
        mode="json"
    )
    return webhook_repo.enqueue(
        session,
        submission_id=submission.id,
        job_id=job.id,
        event_type=event_type,
        url=url,
        payload=payload,
        now=now,
    )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

_CLAIM_DELIVERY_SQL = text(
    """
    WITH claimed AS (
        SELECT id
        FROM webhook_deliveries
        WHERE status IN ('pending', 'failed')
          AND next_attempt_at <= :now
        ORDER BY next_attempt_at, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE webhook_deliveries d
    SET status = 'delivering',
        next_attempt_at = :lease_until
    FROM claimed c
    WHERE d.id = c.id
    RETURNING d.id;
    """
)

_DEAD_LETTER_DELIVERIES_SQL = text(
    """
    UPDATE webhook_deliveries
    SET status = 'dead',
        attempts = attempts + 1,
        last_error = COALESCE(last_error, 'delivery lease expired before completion')
    WHERE status = 'delivering'
      AND next_attempt_at <= :now
      AND attempts + 1 >= :max_attempts
    """
)

_RECLAIM_DELIVERIES_SQL = text(
    """
    UPDATE webhook_deliveries
    SET status = 'pending',
        attempts = attempts + 1,
        next_attempt_at = :now
    WHERE status = 'delivering'
      AND next_attempt_at <= :now
    """
)


@dataclass(frozen=True)
class DeliveryOutcome:
    delivery_id: str
    outcome: str
    status_code: int | None = None
    error: str | None = None


def claim_due_delivery(
    session: Session, now: datetime, *, lease_seconds: int = 900
) -> WebhookDelivery | None:
    row = (
        session.execute(
            _CLAIM_DELIVERY_SQL,
            {"now": now, "lease_until": now + timedelta(seconds=lease_seconds)},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return session.get(WebhookDelivery, row["id"])


def reclaim_stale_deliveries(
    session: Session, *, now: datetime, max_attempts: int | None = None
) -> int:
    """Return deliveries whose claim lease expired to the retry queue.

    A crash-reclaim spends one attempt, like any other delivery failure, so a row whose
    lease keeps expiring reaches ``dead`` at the configured maximum instead of looping.
    """

    if max_attempts is not None:
        session.execute(_DEAD_LETTER_DELIVERIES_SQL, {"now": now, "max_attempts": max_attempts})
    result = cast(Any, session.execute(_RECLAIM_DELIVERIES_SQL, {"now": now}))
    return int(result.rowcount or 0)


def _next_attempt(now: datetime, attempts: int) -> datetime:
    index = min(max(attempts - 1, 0), len(RETRY_SCHEDULE_SECONDS) - 1)
    return now + timedelta(seconds=RETRY_SCHEDULE_SECONDS[index])


def _is_terminal_4xx(status_code: int, attempts: int, max_attempts: int) -> bool:
    # "Other 4xx retry twice, then dead" — but never exceed the configured limit.
    return (
        400 <= status_code < 500
        and status_code not in (408, 410, 429)
        and attempts >= min(3, max_attempts)
    )


def deliver_once(
    session: Session,
    delivery: WebhookDelivery,
    client: Client,
    *,
    settings: Settings,
    clock: Clock,
    http_client: httpx.Client | None = None,
) -> DeliveryOutcome:
    now = clock.now()
    if not client.webhook_secret_ciphertext:
        delivery.status = "dead"
        delivery.last_error = "client has no webhook secret"
        delivery.attempts += 1
        return DeliveryOutcome(delivery.id, "dead", error=delivery.last_error)

    try:
        ensure_safe_target(delivery.url, settings)
    except WebhookTargetUnavailable as exc:
        # A transient lookup failure must not burn the delivery: keep it retryable.
        delivery.attempts += 1
        delivery.last_error = exc.reason
        if delivery.attempts >= settings.webhook_max_attempts:
            delivery.status = "dead"
            return DeliveryOutcome(delivery.id, "dead", error=exc.reason)
        delivery.status = "failed"
        delivery.next_attempt_at = _next_attempt(now, delivery.attempts)
        return DeliveryOutcome(delivery.id, "retry", error=exc.reason)
    except (errors.DomainError, ValueError) as exc:
        delivery.status = "dead"
        delivery.attempts += 1
        if isinstance(exc, errors.DomainError):
            delivery.last_error = exc.message
            return DeliveryOutcome(delivery.id, "dead", error=exc.code)
        delivery.last_error = str(exc)
        return DeliveryOutcome(delivery.id, "dead", error="invalid_url")

    try:
        secret = decrypt_webhook_secret(client.webhook_secret_ciphertext, settings.secret_key)
    except errors.DomainError as exc:
        # A key rotation or a wrong SECRET_KEY must not leave the row leased forever.
        delivery.status = "dead"
        delivery.attempts += 1
        delivery.last_error = exc.message
        return DeliveryOutcome(delivery.id, "dead", error=exc.code)
    body = stable_json(delivery.payload).encode("utf-8")
    timestamp = int(now.timestamp())
    headers = {
        "Content-Type": "application/json",
        "X-TradeExtract-Event": delivery.event_type,
        "X-TradeExtract-Delivery": delivery.id,
        "X-TradeExtract-Timestamp": str(timestamp),
        "X-TradeExtract-Signature": signature_for(secret, timestamp, body),
    }

    delivery.attempts += 1
    status_code: int | None = None
    error: str | None = None
    try:
        if http_client is None:
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=settings.webhook_connect_timeout_seconds,
                    read=settings.webhook_timeout_seconds,
                    write=settings.webhook_timeout_seconds,
                    pool=settings.webhook_connect_timeout_seconds,
                ),
                follow_redirects=False,
            ) as client_http:
                response = client_http.post(delivery.url, content=body, headers=headers)
        else:
            response = http_client.post(delivery.url, content=body, headers=headers)
        status_code = response.status_code
    except httpx.HTTPError as exc:
        error = type(exc).__name__

    delivery.last_status_code = status_code
    delivery.last_error = error

    if status_code is not None and 200 <= status_code < 300:
        delivery.status = "delivered"
        delivery.delivered_at = now
        return DeliveryOutcome(delivery.id, "delivered", status_code=status_code)

    if status_code == 410:
        delivery.status = "dead"
        return DeliveryOutcome(delivery.id, "dead", status_code=410, error="gone")

    retry = True
    if status_code is not None:
        terminal_4xx = _is_terminal_4xx(
            status_code, delivery.attempts, settings.webhook_max_attempts
        )
        if terminal_4xx or (
            status_code >= 500 and delivery.attempts >= settings.webhook_max_attempts
        ):
            retry = False
    if delivery.attempts >= settings.webhook_max_attempts:
        retry = False

    if retry:
        delivery.status = "failed"
        delivery.next_attempt_at = _next_attempt(now, delivery.attempts)
        return DeliveryOutcome(
            delivery.id, "retry", status_code=status_code, error=error or f"http_{status_code}"
        )

    delivery.status = "dead"
    return DeliveryOutcome(
        delivery.id, "dead", status_code=status_code, error=error or f"http_{status_code}"
    )


def payload_sha(payload: dict[str, Any]) -> str:
    return sha256_bytes(stable_json(payload).encode("utf-8"))
