from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.segment import Segment


def create_for_segment(
    session: Session,
    *,
    document_id: str,
    job_id: str,
    submission_id: str,
    file_id: str,
    segment_id: str,
    client_id: str,
    document_type: str,
    source_file_hash: str,
    document_hash: str,
    idempotency_key: str,
    page_start: int,
    page_end: int,
    template_fingerprint: str,
    now: datetime,
) -> Document:
    document = Document(
        id=document_id,
        job_id=job_id,
        submission_id=submission_id,
        file_id=file_id,
        segment_id=segment_id,
        client_id=client_id,
        document_type=document_type,
        source_file_hash=source_file_hash,
        document_hash=document_hash,
        idempotency_key=idempotency_key,
        status="queued",
        template_fingerprint=template_fingerprint,
        page_start=page_start,
        page_end=page_end,
        created_at=now,
        updated_at=now,
    )
    session.add(document)
    return document


def exists_for_file(session: Session, file_id: str) -> bool:
    stmt = select(exists().where(Document.file_id == file_id))
    return bool(session.execute(stmt).scalar())


def delete_for_file(session: Session, file_id: str) -> None:
    session.execute(delete(Document).where(Document.file_id == file_id))


def list_for_submission(
    session: Session, submission_id: str
) -> list[tuple[Document, Segment, File]]:
    """Documents with the segment that owns their pages and the source file."""

    stmt = (
        select(Document, Segment, File)
        .join(Segment, Document.segment_id == Segment.id)
        .join(File, Document.file_id == File.id)
        .where(Document.submission_id == submission_id)
        .order_by(File.created_at, Document.page_start, Document.id)
    )
    return [
        (document, segment, file_row) for document, segment, file_row in session.execute(stmt).all()
    ]
