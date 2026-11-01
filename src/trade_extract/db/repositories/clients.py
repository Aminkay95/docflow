from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from trade_extract.db.models.api_key import ApiKey
from trade_extract.db.models.client import Client


def get_api_key_by_prefix(session: Session, key_prefix: str) -> ApiKey | None:
    stmt = (
        select(ApiKey)
        .options(joinedload(ApiKey.client))
        .where(ApiKey.key_prefix == key_prefix, ApiKey.revoked_at.is_(None))
    )
    return session.execute(stmt).scalars().first()


def get_revoked_api_key_by_prefix(session: Session, key_prefix: str) -> ApiKey | None:
    """Look up a revoked key so authentication can name revocation explicitly."""

    stmt = select(ApiKey).where(ApiKey.key_prefix == key_prefix, ApiKey.revoked_at.isnot(None))
    return session.execute(stmt).scalars().first()


def get_client(session: Session, client_id: str) -> Client | None:
    return session.get(Client, client_id)


def get_client_by_slug(session: Session, slug: str) -> Client | None:
    return session.execute(select(Client).where(Client.slug == slug)).scalars().first()
