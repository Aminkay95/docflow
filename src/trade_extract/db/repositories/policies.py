from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.policy import Policy


def get_active(session: Session) -> Policy | None:
    return session.execute(select(Policy).where(Policy.status == "active")).scalars().first()


def get_by_version(session: Session, version: int) -> Policy | None:
    return session.execute(select(Policy).where(Policy.version == version)).scalars().first()


def create_active(
    session: Session,
    *,
    policy_id: str,
    version: int,
    name: str,
    config: dict[str, object],
    now: datetime,
) -> Policy:
    policy = Policy(
        id=policy_id,
        version=version,
        name=name,
        config=config,
        status="active",
        activated_at=now,
    )
    session.add(policy)
    return policy
