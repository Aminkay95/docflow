from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.task import JobTask
from trade_extract.domain.ids import new_id


def enqueue(
    session: Session,
    *,
    job_id: str,
    kind: str,
    now: datetime,
    submission_id: str | None = None,
    document_id: str | None = None,
    segment_id: str | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    priority: int = 100,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> JobTask:
    if dedupe_key is not None:
        existing = (
            session.execute(select(JobTask).where(JobTask.dedupe_key == dedupe_key))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
    task = JobTask(
        id=new_id("task_"),
        job_id=job_id,
        submission_id=submission_id,
        document_id=document_id,
        segment_id=segment_id,
        kind=kind,
        status="queued",
        priority=priority,
        run_after=run_after or now,
        attempts=0,
        max_attempts=max_attempts,
        dedupe_key=dedupe_key,
        payload=payload or {},
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.flush()
    return task


def get(session: Session, task_id: str) -> JobTask | None:
    return session.get(JobTask, task_id)


def rearm(
    session: Session,
    *,
    dedupe_key: str,
    job_id: str,
    kind: str,
    now: datetime,
    submission_id: str | None = None,
    document_id: str | None = None,
    segment_id: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int | None = None,
    run_after: datetime | None = None,
) -> JobTask:
    """Re-open an existing task with the same dedupe key, or enqueue a new one.

    Finalize tasks and re-submitted files need a fresh attempt even when a previous
    task with the same dedupe key already succeeded or went dead.
    """

    existing = (
        session.execute(select(JobTask).where(JobTask.dedupe_key == dedupe_key)).scalars().first()
    )
    if existing is None:
        return enqueue(
            session,
            job_id=job_id,
            kind=kind,
            now=now,
            submission_id=submission_id,
            document_id=document_id,
            segment_id=segment_id,
            payload=payload,
            dedupe_key=dedupe_key,
            priority=priority,
            max_attempts=max_attempts if max_attempts is not None else 3,
            run_after=run_after,
        )
    existing.status = "queued"
    existing.attempts = 0
    existing.run_after = run_after or now
    existing.locked_by = None
    existing.locked_at = None
    existing.started_at = None
    existing.finished_at = None
    existing.last_error = None
    existing.last_error_code = None
    existing.submission_id = submission_id
    existing.document_id = document_id
    existing.segment_id = segment_id
    if payload is not None:
        existing.payload = payload
    if max_attempts is not None:
        existing.max_attempts = max_attempts
    existing.updated_at = now
    session.flush()
    return existing


def list_for_job(session: Session, job_id: str) -> list[JobTask]:
    stmt = select(JobTask).where(JobTask.job_id == job_id).order_by(JobTask.created_at)
    return list(session.execute(stmt).scalars().all())
