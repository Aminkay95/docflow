from __future__ import annotations

from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import files as files_repo
from trade_extract.db.repositories import jobs as jobs_repo
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.db.repositories import tasks as tasks_repo
from trade_extract.domain.clock import Clock


def handle(
    session: Session,
    task: JobTask,
    *,
    settings: Settings,
    clock: Clock,
) -> None:
    """Stub prepare step: mark the file processed and fan in when the last one lands.

    Story 2 replaces the body with page rasterisation and segmentation; the task
    contract (ids only, idempotent) stays the same.
    """

    del settings
    now = clock.now()
    payload = task.payload or {}
    file_id = payload.get("file_id")
    if not file_id:
        return
    file_row = files_repo.get(session, file_id)
    if file_row is None:
        return

    files_repo.mark_processing(session, file_row)
    submission = session.get(Submission, file_row.submission_id)
    if submission is not None:
        submissions_repo.mark_processing(session, submission)

    job = session.get(Job, file_row.job_id)
    if job is not None:
        jobs_repo.mark_processing(session, job, now=now)

    files_repo.mark_processed(session, file_row, now=now)

    if submission is not None and submission.status in ("failed", "completed"):
        # A terminal submission's finalize task must not be re-armed (it may be dead).
        return

    siblings = files_repo.list_for_submission(session, file_row.submission_id)
    if all(row.status in ("processed", "failed") for row in siblings):
        tasks_repo.rearm(
            session,
            dedupe_key=f"finalize_submission:{file_row.submission_id}",
            job_id=file_row.job_id,
            submission_id=file_row.submission_id,
            kind="finalize_submission",
            payload={
                "submission_id": file_row.submission_id,
                "job_id": file_row.job_id,
            },
            now=now,
        )
