from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.file import File


def find_by_hashes(session: Session, job_id: str, hashes: list[str]) -> dict[str, File]:
    if not hashes:
        return {}
    stmt = select(File).where(File.job_id == job_id, File.sha256.in_(hashes))
    return {row.sha256: row for row in session.execute(stmt).scalars().all()}


def insert(
    session: Session,
    *,
    file_id: str,
    submission_id: str,
    job_id: str,
    client_id: str,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    storage_key: str,
    page_count: int,
    now: datetime,
) -> File:
    file_row = File(
        id=file_id,
        submission_id=submission_id,
        job_id=job_id,
        client_id=client_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_key=storage_key,
        page_count=page_count,
        status="received",
        created_at=now,
    )
    session.add(file_row)
    session.flush()
    return file_row


def get(session: Session, file_id: str) -> File | None:
    return session.get(File, file_id)


def list_for_submission(session: Session, submission_id: str) -> list[File]:
    stmt = (
        select(File).where(File.submission_id == submission_id).order_by(File.created_at, File.id)
    )
    return list(session.execute(stmt).scalars().all())


def mark_processing(session: Session, file_row: File) -> None:
    if file_row.status == "received":
        file_row.status = "processing"


def mark_processed(session: Session, file_row: File, *, now: datetime) -> bool:
    """Mark a file processed. Returns True when this call changed the row."""

    if file_row.status == "processed":
        return False
    file_row.status = "processed"
    file_row.processed_at = now
    return True


def mark_failed(session: Session, file_row: File, *, code: str, message: str) -> None:
    file_row.status = "failed"
    file_row.error_code = code
    file_row.error_message = message
