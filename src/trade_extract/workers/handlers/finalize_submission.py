from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import files as files_repo
from trade_extract.db.repositories import jobs as jobs_repo
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.domain.clock import Clock
from trade_extract.services.webhooks import enqueue_completion


def _rearm(task: JobTask, *, now: datetime, poll_seconds: float) -> None:
    """Put the task back without spending a retry while sibling work is active."""

    task.status = "queued"
    task.locked_by = None
    task.locked_at = None
    task.finished_at = None
    task.run_after = now + timedelta(seconds=max(poll_seconds, 0.1))
    if task.attempts > 0:
        task.attempts -= 1
    task.updated_at = now


def handle(
    session: Session,
    task: JobTask,
    *,
    settings: Settings,
    clock: Clock,
) -> None:
    """Finalize a submission exactly once and write the webhook outbox row."""

    now = clock.now()
    payload = task.payload or {}
    submission_id = payload.get("submission_id")
    if not submission_id:
        return
    submission = session.get(Submission, submission_id)
    if submission is None:
        return
    # Never overwrite a terminal submission (especially not a failed one).
    if submission.status in ("completed", "failed"):
        return

    file_rows = files_repo.list_for_submission(session, submission.id)
    if any(row.status in ("received", "processing") for row in file_rows):
        # The last prepare_file task will re-arm this task after they finish.
        _rearm(task, now=now, poll_seconds=settings.worker_poll_seconds)
        return

    job = session.get(Job, submission.job_id)
    if job is None:
        return

    failed = [row for row in file_rows if row.status == "failed"]
    if failed:
        first = failed[0]
        code = first.error_code or "file_failed"
        message = first.error_message or f"Document '{first.filename}' failed to process."
        submissions_repo.mark_failed(session, submission, now=now, code=code, message=message)
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
        return

    submissions_repo.mark_completed(session, submission, now=now)
    if submissions_repo.active_count_for_job(session, job.id) == 0:
        jobs_repo.mark_completed(session, job, now=now)

    enqueue_completion(
        session,
        submission=submission,
        job=job,
        settings=settings,
        now=now,
    )
