from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.webhook import WebhookDelivery
from trade_extract.domain.hashing import sha256_bytes, stable_json
from trade_extract.domain.ids import new_id


def enqueue(
    session: Session,
    *,
    submission_id: str,
    job_id: str,
    event_type: str,
    url: str,
    payload: dict[str, Any],
    now: datetime,
) -> WebhookDelivery:
    existing = (
        session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.submission_id == submission_id,
                WebhookDelivery.event_type == event_type,
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    body = stable_json(payload).encode("utf-8")
    delivery = WebhookDelivery(
        id=new_id("whd_"),
        submission_id=submission_id,
        job_id=job_id,
        event_type=event_type,
        url=url,
        payload=payload,
        payload_sha256=sha256_bytes(body),
        status="pending",
        attempts=0,
        next_attempt_at=now,
        created_at=now,
    )
    session.add(delivery)
    session.flush()
    return delivery


def get(session: Session, delivery_id: str) -> WebhookDelivery | None:
    return session.get(WebhookDelivery, delivery_id)


def list_for_submission(session: Session, submission_id: str) -> list[WebhookDelivery]:
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.submission_id == submission_id)
        .order_by(WebhookDelivery.created_at)
    )
    return list(session.execute(stmt).scalars().all())
