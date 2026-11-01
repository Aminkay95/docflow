from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.job import Job
from trade_extract.domain import errors
from trade_extract.domain.ids import new_id


def get_for_update(session: Session, client_id: str, job_ref: str) -> Job | None:
    return (
        session.execute(
            select(Job).where(Job.client_id == client_id, Job.job_ref == job_ref).with_for_update()
        )
        .scalars()
        .first()
    )


def get_by_id(session: Session, job_id: str, client_id: str) -> Job | None:
    return (
        session.execute(select(Job).where(Job.id == job_id, Job.client_id == client_id))
        .scalars()
        .first()
    )


def create(
    session: Session,
    *,
    client_id: str,
    job_ref: str | None,
    policy_id: str,
    now: datetime,
) -> Job:
    job = Job(
        id=new_id("job_"),
        client_id=client_id,
        job_ref=job_ref,
        status="queued",
        policy_id=policy_id,
        document_count=0,
        open_review_count=0,
        open_conflict_count=0,
        created_at=now,
        updated_at=now,
        last_submission_at=now,
    )
    session.add(job)
    session.flush()
    return job


def upsert_for_submission(
    session: Session,
    *,
    client_id: str,
    job_ref: str | None,
    policy_id: str,
    now: datetime,
) -> Job:
    """Reuse one job per ``(client, job_ref)``; reopen completed jobs.

    An omitted ``job_ref`` always creates a fresh, non-accumulating job. A failed job
    refuses the submission because its retained records cannot be reconciled safely.
    """

    if job_ref is None:
        return create(session, client_id=client_id, job_ref=None, policy_id=policy_id, now=now)

    job = get_for_update(session, client_id, job_ref)
    if job is None:
        return create(session, client_id=client_id, job_ref=job_ref, policy_id=policy_id, now=now)

    if job.status == "failed":
        raise errors.job_failed()

    if job.status == "completed":
        job.status = "queued"
        job.terminal_at = None
        job.terminal_reason = None

    job.last_submission_at = now
    job.updated_at = now
    return job


def mark_completed(session: Session, job: Job, *, now: datetime) -> None:
    job.status = "completed"
    job.terminal_at = now
    job.terminal_reason = "completed"
    job.updated_at = now
    session.flush()


def mark_processing(session: Session, job: Job, *, now: datetime) -> None:
    if job.status == "queued":
        job.status = "processing"
    job.updated_at = now


def mark_failed(session: Session, job: Job, *, now: datetime) -> None:
    job.status = "failed"
    job.terminal_at = now
    job.terminal_reason = "failed"
    job.updated_at = now
    session.flush()
