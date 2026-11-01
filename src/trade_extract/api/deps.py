from __future__ import annotations

import hmac
from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.api_key import ApiKey
from trade_extract.db.models.client import Client
from trade_extract.db.repositories import clients as clients_repo
from trade_extract.domain import errors
from trade_extract.domain.clock import Clock
from trade_extract.domain.hashing import hash_api_key
from trade_extract.services.storage import ObjectStorage


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_session(request: Request) -> Generator[Session, None, None]:
    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


def _hash_key(pepper: str, token: str) -> str:
    return hash_api_key(pepper, token)


def get_api_key(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> ApiKey:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise errors.unauthorized()

    prefix = token[:16]
    api_key = clients_repo.get_api_key_by_prefix(session, prefix)
    if api_key is None:
        revoked = clients_repo.get_revoked_api_key_by_prefix(session, prefix)
        if revoked is not None:
            raise errors.unauthorized(
                "This API key has been revoked. Use a current key or create a new one."
            )
        raise errors.unauthorized(
            "This API key is not recognised. Use a current key or create a new one."
        )

    expected = _hash_key(settings.api_key_pepper, token)
    if not hmac.compare_digest(expected, api_key.key_hash):
        raise errors.unauthorized(
            "This API key is not recognised. Use a current key or create a new one."
        )
    if api_key.revoked_at is not None:
        raise errors.unauthorized(
            "This API key has been revoked. Use a current key or create a new one."
        )
    if api_key.expires_at is not None and api_key.expires_at <= clock.now():
        raise errors.unauthorized("This API key has expired. Create a new key and resubmit.")
    if api_key.client.status != "active":
        raise errors.client_inactive(api_key.client.status)

    api_key.last_used_at = clock.now()
    try:
        session.commit()
    except Exception:  # noqa: BLE001 - last-used tracking must never deny a valid key
        session.rollback()
    return api_key


def get_current_client(api_key: ApiKey = Depends(get_api_key)) -> Client:
    return api_key.client


def require_scope(scope: str):
    def dependency(api_key: ApiKey = Depends(get_api_key)) -> Client:
        if scope not in api_key.scopes:
            raise errors.forbidden(scope)
        return api_key.client

    return dependency
