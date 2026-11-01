from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import files as files_repo
from trade_extract.db.repositories import jobs as jobs_repo
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.services.webhooks import enqueue_completion

STALE_REQUEUE_LIMIT_CODE = "stale_requeue_limit"

# The only hand-written SQL outside repositories, because it is the queue contract.
CLAIM_SQL = text(
    """
    WITH claimed AS (
        SELECT id
        FROM job_tasks
        WHERE status = 'queued'
          AND run_after <= :now
          AND kind = ANY(:kinds)
        ORDER BY priority DESC, run_after, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE job_tasks t
    SET status = 'running',
        locked_by = :worker_id,
        locked_at = now(),
        started_at = COALESCE(t.started_at, now()),
        attempts = t.attempts + 1
    FROM claimed c
    WHERE t.id = c.id
    RETURNING t.id;
    """
).bindparams(bindparam("kinds", type_=ARRAY(String)))


def claim_task(session: Session, worker_id: str, kinds: list[str], now: datetime) -> JobTask | None:
    row = (
        session.execute(CLAIM_SQL, {"worker_id": worker_id, "kinds": kinds, "now": now})
        .mappings()
        .first()
    )
    if row is None:
        return None
    return session.get(JobTask, row["id"])


def mark_succeeded(session: Session, task: JobTask, *, now: datetime) -> None:
    task.status = "succeeded"
    task.finished_at = now
    task.locked_by = None
    task.locked_at = None
    task.last_error = None
    task.last_error_code = None
    task.updated_at = now


def backoff_seconds(
    attempts: int,
    *,
    base: int = 2,
    max_seconds: int = 300,
    jitter: float | None = None,
) -> float:
    delay = min(max_seconds, base ** max(attempts, 0))
    extra = random.uniform(0, 30) if jitter is None else jitter
    return float(delay + extra)


def mark_failed(
    session: Session,
    task: JobTask,
    *,
    now: datetime,
    error_code: str,
    error: str,
    retry: bool,
    jitter: float | None = None,
    backoff_base: int = 2,
    backoff_max: int = 300,
) -> None:
    task.last_error_code = error_code
    task.last_error = error[:2000]
    task.finished_at = now
    task.locked_by = None
    task.locked_at = None
    task.updated_at = now
    if retry and task.attempts < task.max_attempts:
        task.status = "queued"
        task.run_after = now + timedelta(
            seconds=backoff_seconds(
                task.attempts,
                base=backoff_base,
                max_seconds=backoff_max,
                jitter=jitter,
            )
        )
    else:
        task.status = "dead"


@dataclass(frozen=True)
class RequeueOutcome:
    requeued: int
    dead: tuple[JobTask, ...] = ()


def requeue_stale(
    session: Session,
    *,
    older_than: datetime,
    now: datetime,
    max_requeues: int | None = None,
) -> RequeueOutcome:
    """Return tasks whose worker disappeared to the queue without spending a retry.

    A crashed attempt is not the task's fault, so the claim increment is given back.
    The number of times one task may be crash-requeued is bounded: past the ceiling the
    task is marked dead for the caller to terminalise.
    """

    stale = (
        session.query(JobTask)
        .filter(JobTask.status == "running", JobTask.locked_at < older_than)
        .all()
    )
    requeued = 0
    dead: list[JobTask] = []
    for task in stale:
        task.requeues += 1
        if max_requeues is not None and task.requeues > max_requeues:
            mark_dead(
                task,
                now=now,
                error_code=STALE_REQUEUE_LIMIT_CODE,
                error=(
                    f"task was crash-requeued {task.requeues} times; the limit is {max_requeues}"
                ),
            )
            dead.append(task)
            continue
        task.status = "queued"
        task.locked_by = None
        task.locked_at = None
        task.run_after = now
        if task.attempts > 0:
            task.attempts -= 1
        task.updated_at = now
        requeued += 1
    return RequeueOutcome(requeued=requeued, dead=tuple(dead))


def mark_dead(
    task: JobTask,
    *,
    now: datetime,
    error_code: str,
    error: str,
) -> None:
    task.status = "dead"
    task.finished_at = now
    task.locked_by = None
    task.locked_at = None
    task.last_error_code = error_code
    task.last_error = error[:2000]
    task.updated_at = now


def terminalise_dead_task(
    session: Session,
    task: JobTask,
    *,
    settings: Settings,
    now: datetime,
    code: str,
    message: str,
) -> None:
    """Failure is terminal: a dead task fails its file and submission, then notifies."""

    payload = task.payload or {}
    submission_id = task.submission_id or payload.get("submission_id")
    file_id = payload.get("file_id")
    if file_id and task.kind == "prepare_file":
        file_row = files_repo.get(session, file_id)
        if file_row is not None:
            files_repo.mark_failed(session, file_row, code=code, message=message)
            submission_id = submission_id or file_row.submission_id

    submission = session.get(Submission, submission_id) if submission_id else None
    job = session.get(Job, task.job_id)
    if submission is None or job is None:
        return

    changed = submissions_repo.mark_failed(session, submission, now=now, code=code, message=message)
    if not changed:
        return

    if submissions_repo.active_count_for_job(session, job.id) == 0:
        jobs_repo.mark_failed(session, job, now=now)

    enqueue_completion(
        session,
        submission=submission,
        job=job,
        settings=settings,
        now=now,
        event_type="result.failed",
    )
