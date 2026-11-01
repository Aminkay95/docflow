from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.domain.ids import new_id


def create(
    session: Session,
    *,
    job: Job,
    client_id: str,
    webhook_url: str | None,
    idempotency_key: str | None,
    request_fingerprint: str | None,
    document_count: int,
    duplicate_count: int,
    now: datetime,
) -> Submission:
    submission = Submission(
        id=new_id("sub_"),
        result_id=new_id("res_"),
        job_id=job.id,
        client_id=client_id,
        status="queued",
        webhook_url=webhook_url,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        document_count=document_count,
        duplicate_count=duplicate_count,
        created_at=now,
    )
    session.add(submission)
    session.flush()
    return submission


def get_by_result_id(session: Session, result_id: str, client_id: str) -> Submission | None:
    return (
        session.execute(
            select(Submission).where(
                Submission.result_id == result_id,
                Submission.client_id == client_id,
            )
        )
        .scalars()
        .first()
    )


def get_by_idempotency_key(
    session: Session, client_id: str, idempotency_key: str
) -> Submission | None:
    return (
        session.execute(
            select(Submission).where(
                Submission.client_id == client_id,
                Submission.idempotency_key == idempotency_key,
            )
        )
        .scalars()
        .first()
    )


def mark_completed(session: Session, submission: Submission, *, now: datetime) -> None:
    submission.status = "completed"
    submission.completed_at = now
    session.flush()


def mark_failed(
    session: Session,
    submission: Submission,
    *,
    now: datetime,
    code: str,
    message: str,
) -> bool:
    """Fail a submission once. Returns True when this call changed the row."""

    if submission.status == "failed":
        return False
    submission.status = "failed"
    submission.error_code = code
    submission.error_message = message
    submission.completed_at = now
    session.flush()
    return True


def mark_processing(session: Session, submission: Submission) -> None:
    if submission.status == "queued":
        submission.status = "processing"


def active_count_for_job(session: Session, job_id: str) -> int:
    stmt = select(Submission).where(
        Submission.job_id == job_id,
        Submission.status.in_(("queued", "processing")),
    )
    return len(list(session.execute(stmt).scalars().all()))
