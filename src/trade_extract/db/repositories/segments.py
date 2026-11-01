from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trade_extract.db.models.segment import Segment


def insert(
    session: Session,
    *,
    segment_id: str,
    file_id: str,
    job_id: str,
    page_start: int,
    page_end: int,
    document_type: str,
    confidence: float,
    fingerprint: str,
    now: datetime,
) -> Segment:
    segment = Segment(
        id=segment_id,
        file_id=file_id,
        job_id=job_id,
        page_start=page_start,
        page_end=page_end,
        document_type=document_type,
        confidence=confidence,
        fingerprint=fingerprint,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(segment)
    return segment


def list_for_file(session: Session, file_id: str) -> list[Segment]:
    stmt = select(Segment).where(Segment.file_id == file_id).order_by(Segment.page_start)
    return list(session.execute(stmt).scalars().all())


def delete_for_file(session: Session, file_id: str) -> None:
    session.execute(delete(Segment).where(Segment.file_id == file_id))
