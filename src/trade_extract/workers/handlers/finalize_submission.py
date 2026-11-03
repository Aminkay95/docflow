from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import DecisionRecord
from trade_extract.db.models.job import Job
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import evidence
from trade_extract.db.repositories import files as files_repo
from trade_extract.db.repositories import jobs as jobs_repo
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.domain.checks import CHECK_VERSION
from trade_extract.domain.clock import Clock
from trade_extract.services.conflicts import detect_conflicts
from trade_extract.services.decisions import effective_policy
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

    job = session.scalar(select(Job).where(Job.id == submission.job_id).with_for_update())
    if job is None:
        return
    session.refresh(submission, with_for_update=True)
    if submission.status in ("completed", "failed"):
        return
    all_documents = list(
        session.scalars(
            select(Document).where(Document.job_id == job.id, Document.client_id == job.client_id)
        )
    )
    current_documents = [d for d in all_documents if d.submission_id == submission.id]
    if any(d.status in ("queued", "processing") for d in all_documents):
        _rearm(task, now=now, poll_seconds=settings.worker_poll_seconds)
        return

    failed = [row for row in file_rows if row.status == "failed"]
    policy = session.get(Policy, job.policy_id)
    if policy is None:
        raise ValueError("missing_policy")
    gate, policy_valid = effective_policy(policy.config)
    drafts = detect_conflicts(
        job.id,
        [
            {
                "document_id": d.id,
                "document_type": d.document_type,
                "fields": d.extraction.get("fields", {}),
                "extension": d.extraction.get("extension", {}),
            }
            for d in all_documents
            if d.extraction
        ],
        tolerance=gate.arithmetic_tolerance,
    )
    evidence.save_conflicts(session, job_id=job.id, client_id=job.client_id, drafts=drafts, now=now)
    conflicts = evidence.conflicts_for(session, job_id=job.id, client_id=job.client_id)
    open_conflicts = [c for c in conflicts if c["status"] == "open"]
    job.open_conflict_count = len(open_conflicts)
    job.open_review_count, _ = evidence.review_counts(session, job.id)
    failed_documents = [d for d in current_documents if d.status == "failed"]
    if failed or failed_documents:
        submissions_repo.mark_failed(
            session,
            submission,
            now=now,
            code=(failed[0].error_code or "file_failed") if failed else "extraction_failed",
            message=(failed[0].error_message or f"Document {failed[0].filename} failed to process.")
            if failed
            else "Invoice extraction failed; retained diagnostics require review.",
        )
    else:
        submissions_repo.mark_completed(session, submission, now=now)
    any_document_failed = any(d.status == "failed" for d in all_documents)
    reasons = ["extraction_failed"] if any_document_failed else []
    if failed:
        reasons.append(f"file_failed:{failed[0].filename}")
    reasons.extend(f"conflict_open:{c['canonical_key']}" for c in open_conflicts)
    if job.open_review_count:
        reasons.append("extraction_review_open")
    job_decision = {
        "value": "failed" if any_document_failed or failed else "review" if reasons else "pass",
        "route": "conflict" if open_conflicts else "extraction" if reasons else "none",
        "reasons": reasons,
        "policy_id": policy.id,
        "policy_version": policy.version,
        "policy_snapshot": gate.model_dump(mode="json"),
        "policy_valid": policy_valid,
        "check_version": CHECK_VERSION,
    }
    session.add(
        DecisionRecord(
            id=f"dec_{submission.id}",
            job_id=job.id,
            document_id=None,
            policy_id=policy.id,
            outcome=job_decision,
        )
    )

    if submissions_repo.active_count_for_job(session, job.id) == 0:
        if reasons:
            job.status = "review_required"
            job.terminal_at = None
            job.terminal_reason = None
        else:
            jobs_repo.mark_completed(session, job, now=now)

    enqueue_completion(
        session,
        submission=submission,
        job=job,
        settings=settings,
        now=now,
        event_type="result.failed"
        if failed or failed_documents
        else "result.review_required"
        if reasons
        else "result.completed",
    )
