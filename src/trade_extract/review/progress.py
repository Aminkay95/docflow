"""Shared progress projection for HTML and authenticated polling."""

from sqlalchemy import case, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import ConflictRecord, ReviewItem
from trade_extract.db.models.file import File
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories.evidence import review_counts
from trade_extract.services.progress import KINDS, STAGES


def _metadata(task):
    metadata = (task.payload or {}).get("progress", {})
    if task.status == "queued" or metadata.get("attempt") != task.attempts:
        return {}
    if task.status == "running":
        token = task.locked_at.isoformat() if task.locked_at else None
        if not token or metadata.get("claim") != token:
            return {}
    elif not metadata.get("claim"):
        return {}
    return metadata


def project(session, job):
    fields_object = case(
        (
            func.jsonb_typeof(Document.extraction["fields"]) == "object",
            Document.extraction["fields"],
        ),
        else_=cast({}, JSONB),
    )
    field_count = (
        select(func.count())
        .select_from(func.jsonb_object_keys(fields_object).table_valued("key"))
        .correlate(Document)
        .scalar_subquery()
    )
    # Polls transfer lifecycle data and a database-computed count, never extraction values.
    documents = list(
        session.execute(
            select(
                Document.id,
                Document.status,
                Document.updated_at,
                Document.file_id,
                Document.page_start,
                Document.page_end,
                field_count.label("field_count"),
            )
            .where(Document.job_id == job.id)
            .order_by(Document.created_at, Document.id)
        )
    )
    files = list(
        session.scalars(select(File).where(File.job_id == job.id).order_by(File.created_at))
    )
    tasks = list(
        session.scalars(
            select(JobTask)
            .where(JobTask.job_id == job.id, JobTask.kind.in_(tuple(KINDS)))
            .order_by(JobTask.created_at, JobTask.id)
        )
    )
    submissions = list(session.scalars(select(Submission).where(Submission.job_id == job.id)))
    latest_submission = max(submissions, key=lambda s: (s.created_at, s.id), default=None)
    started_at = latest_submission.created_at if latest_submission else job.created_at
    active = [t for t in tasks if t.status in ("queued", "running")]
    processing = (
        bool(active)
        or any(d.status in ("queued", "processing") for d in documents)
        or any(s.status in ("queued", "processing") for s in submissions)
        or job.status in ("queued", "processing")
    )
    reviews = sum(review_counts(session, job.id))
    conflicts = session.scalar(
        select(ConflictRecord.id)
        .where(ConflictRecord.job_id == job.id, ConflictRecord.status == "open")
        .limit(1)
    )
    failed = (
        job.status == "failed"
        or any(d.status == "failed" for d in documents)
        or any(f.status == "failed" for f in files)
    )
    state = (
        "processing"
        if processing
        else "failed"
        if failed
        else "review"
        if reviews or conflicts
        else "complete"
    )
    # A finalizer can run while siblings are unfinished; it must not hide their work.
    current = min(
        active,
        key=lambda task: (task.kind == "finalize_submission", task.status != "running"),
        default=None,
    )
    observed: dict[str, str] = {}
    stopped = set()
    running = set()
    for task in tasks:
        metadata = _metadata(task)
        for row in metadata.get("stages", []):
            key = row.get("stage")
            if key in STAGES and isinstance(row.get("at"), str):
                observed[key] = max(observed.get(key, ""), row["at"])
                if key == metadata.get("stage") and task.status == "running":
                    running.add(key)
                elif key == metadata.get("stage") and task.status in ("dead", "failed"):
                    stopped.add(key)
    stage = None
    if current and current.status == "running":
        stage = _metadata(current).get("stage") or KINDS[current.kind]
        running.add(stage)
    stages = [
        {
            "id": key,
            "label": label,
            "at": observed.get(key),
            "status": "active"
            if key in running
            else "stopped"
            if key in stopped
            else "partial"
            if processing
            else "complete",
        }
        for key, label in STAGES.items()
        if key in observed or key in running
    ]
    updated = max(observed.values(), default=None)
    # The panel covers the whole job: an earlier upload can finish after a newer
    # one. Freeze only at the latest reliable completion across that work.
    completion_times = [
        submission.completed_at
        for submission in submissions
        if submission.completed_at and submission.completed_at >= started_at
    ]
    if state == "failed":
        completion_times.extend(
            task.finished_at
            for task in tasks
            if task.status in ("failed", "dead")
            and task.finished_at
            and task.finished_at >= started_at
        )
    completed_at = max(completion_times, default=None)
    title = {
        "processing": STAGES.get(stage or "", "Waiting to process"),
        "failed": "Processing ended with a failure",
        "review": "Ready for review",
        "complete": "Processing complete",
    }[state]
    if current and current.status == "queued" and current.attempts:
        title = "Waiting to retry processing"
    # Only identities, lifecycle timestamps and counts; never extraction values or diagnostics.
    review_versions = session.execute(
        select(ReviewItem.id, ReviewItem.status, ReviewItem.assigned_to, ReviewItem.updated_at)
        .where(ReviewItem.job_id == job.id)
        .order_by(ReviewItem.id)
    )
    version = "|".join(
        [
            state,
            str(reviews),
            *[
                f"{d.id}:{d.file_id}:{d.page_start}:{d.page_end}:"
                + (
                    "pending"
                    if d.status in ("queued", "processing")
                    else f"{d.status}:{d.updated_at.isoformat()}:{d.field_count}"
                )
                for d in documents
            ],
            *[
                f"{r.id}:{r.status}:{r.assigned_to}:{r.updated_at.isoformat()}"
                for r in review_versions
            ],
        ]
    )
    return {
        "state": state,
        "processing": processing,
        "title": title,
        "started_at": started_at.isoformat(),
        "updated_at": updated,
        "terminal_at": completed_at.isoformat() if not processing and completed_at else None,
        "stages": stages,
        "documents": len(documents),
        "documents_ready": sum(d.status not in ("queued", "processing") for d in documents),
        "fields": sum(d.field_count for d in documents),
        "reviews": reviews,
        "version": version,
        "files": [{"name": f.filename, "status": f.status} for f in files],
    }
