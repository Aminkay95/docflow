"""Validated client administration shared by console and provisioning script.

Operations flush, but never commit: the caller owns the atomic transaction.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.api_key import ApiKey
from trade_extract.db.models.client import Client
from trade_extract.domain import errors
from trade_extract.domain.hashing import hash_api_key
from trade_extract.domain.ids import new_id
from trade_extract.services.limits import validate_webhook_url
from trade_extract.services.webhooks import encrypt_webhook_secret

DEFAULT_SCOPES = ["extract:write", "results:read", "account:read", "account:write"]


def generate_api_key(environment: str) -> str:
    return ("tx_live_" if environment == "production" else "tx_test_") + secrets.token_urlsafe(32)


def text_field(value: str, field: str, maximum: int = 200) -> str:
    value = value.strip()
    if not value or len(value) > maximum:
        raise errors.invalid_request(field, f"must contain 1 to {maximum} characters")
    return value


def validate_scopes(scopes: list[str]) -> list[str]:
    if not scopes or any(scope not in DEFAULT_SCOPES for scope in scopes):
        raise errors.invalid_request("scopes", "select one or more known scopes")
    return list(dict.fromkeys(scopes))


def validate_expiry(expires_at: datetime | None, now: datetime) -> None:
    if expires_at is not None and (
        expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= now
    ):
        raise errors.invalid_request("expires_at", "must be a future timestamp with a timezone")


def client_for_update(session: Session, client_id: str) -> Client:
    client = session.scalar(
        select(Client)
        .where(Client.id == client_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if client is None:
        raise errors.DomainError("client_not_found", "Client not found.", http_status=404)
    return client


def update_details(
    session: Session, client: Client, *, name: str, slug: str, now: datetime
) -> None:
    name = text_field(name, "name")
    slug = text_field(slug, "slug", 100)
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", slug):
        raise errors.invalid_request(
            "slug", "use lowercase letters, numbers, hyphens or underscores"
        )
    if session.scalar(select(Client.id).where(Client.slug == slug, Client.id != client.id)):
        raise errors.DomainError(
            "client_slug_conflict", "This client slug already exists.", http_status=409
        )
    client.name, client.slug, client.updated_at = name, slug, now


def create_key(
    session: Session,
    client: Client,
    *,
    name: str,
    scopes: list[str],
    expires_at: datetime | None,
    settings: Settings,
    now: datetime,
) -> tuple[ApiKey, str]:
    name = text_field(name, "name")
    scopes = validate_scopes(scopes)
    validate_expiry(expires_at, now)
    if client.status == "terminated":
        raise errors.invalid_request("status", "terminated accounts cannot receive new keys")
    token = generate_api_key(settings.environment)
    key = ApiKey(
        id=new_id("key_"),
        client_id=client.id,
        name=name,
        key_prefix=token[:16],
        key_hash=hash_api_key(settings.api_key_pepper, token),
        scopes=scopes,
        expires_at=expires_at,
        created_at=now,
    )
    session.add(key)
    session.flush()
    return key, token


def configure_webhook(
    client: Client, *, url: str | None, enabled: bool, settings: Settings, now: datetime
) -> None:
    url = validate_webhook_url(url, settings)
    if enabled and not url:
        raise errors.invalid_request("webhook_url", "a URL is required when delivery is enabled")
    client.webhook_url, client.webhook_enabled, client.updated_at = url, enabled, now


def rotate_webhook_secret(client: Client, *, settings: Settings, now: datetime) -> str:
    secret = "whsec_" + secrets.token_urlsafe(32)
    client.webhook_secret_ciphertext = encrypt_webhook_secret(secret, settings.secret_key)
    client.updated_at = now
    return secret


def provision(
    session: Session,
    *,
    name: str,
    slug: str,
    webhook_url: str | None,
    scopes: list[str],
    settings: Settings,
    now: datetime,
) -> tuple[Client, dict]:
    # Validate everything before adding anything to the transaction.
    scopes = validate_scopes(scopes)
    url = validate_webhook_url(webhook_url, settings)
    client = Client(id=new_id("cli_"), status="active", created_at=now, updated_at=now)
    update_details(session, client, name=name, slug=slug, now=now)
    configure_webhook(client, url=url, enabled=bool(url), settings=settings, now=now)
    secret = rotate_webhook_secret(client, settings=settings, now=now)
    session.add(client)
    session.flush()
    key, token = create_key(
        session,
        client,
        name=f"{client.slug} default",
        scopes=scopes,
        expires_at=None,
        settings=settings,
        now=now,
    )
    return client, dict(
        client_id=client.id,
        api_key=token,
        key_prefix=key.key_prefix,
        webhook_secret=secret,
        scopes=scopes,
    )


def revoke_key(session: Session, client: Client, key_id: str, *, now: datetime) -> None:
    key = session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.client_id == client.id).with_for_update()
    )
    if key is None:
        raise errors.DomainError("key_not_found", "Key not found for this client.", http_status=404)
    if key.revoked_at is None:
        key.revoked_at = now


def change_status(client: Client, *, status: str, confirmed: bool, now: datetime) -> None:
    if status not in ("active", "suspended", "terminated"):
        raise errors.invalid_request("status", "choose active, suspended or terminated")
    if client.status == "terminated":
        raise errors.DomainError(
            "invalid_transition", "Terminated clients cannot change status.", http_status=409
        )
    if status == client.status:
        raise errors.DomainError(
            "invalid_transition", "Client already has this status.", http_status=409
        )
    if status == "terminated" and not confirmed:
        raise errors.invalid_request("confirmed", "explicitly confirm permanent termination")
    client.status = status
    client.terminated_at = now if status == "terminated" else None
    client.updated_at = now
