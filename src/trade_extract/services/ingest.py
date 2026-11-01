from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.submission import Submission
from trade_extract.db.repositories import files as files_repo
from trade_extract.db.repositories import jobs as jobs_repo
from trade_extract.db.repositories import policies as policies_repo
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.db.repositories import tasks as tasks_repo
from trade_extract.domain import errors
from trade_extract.domain.clock import Clock
from trade_extract.domain.hashing import request_fingerprint
from trade_extract.domain.ids import new_id
from trade_extract.services.limits import ValidatedSubmission
from trade_extract.services.storage import ObjectStorage, source_key
from trade_extract.services.webhooks import enqueue_completion

#: Existing files in these states are re-processed rather than counted as duplicates.
RETRYABLE_FILE_STATUSES = ("failed", "purged")


@dataclass(frozen=True)
class AcceptedSubmission:
    result_id: str
    job_id: str
    job_ref: str | None
    status: str
    submitted_documents: int
    duplicate_documents: int
    status_url: str
    created_at: datetime


def accepted_from_submission(
    submission: Submission, job: Job, settings: Settings
) -> AcceptedSubmission:
    """Build the frozen acceptance body.

    The ``202`` body always reports ``status: queued``, including on replay after the
    submission completed and for the all-duplicates case; polling reveals completion.
    It intentionally does not advertise a job URL until a job-read route exists.
    """

    return AcceptedSubmission(
        result_id=submission.result_id,
        job_id=job.id,
        job_ref=job.job_ref,
        status="queued",
        submitted_documents=submission.document_count,
        duplicate_documents=submission.duplicate_count,
        status_url=f"{settings.base_url}/v1/results/{submission.result_id}",
        created_at=submission.created_at,
    )


def _constraint_name(exc: IntegrityError) -> str:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    if name:
        return str(name)
    text = str(exc.orig if exc.orig is not None else exc)
    for candidate in (
        "ux_submissions_client_idempotency",
        "ux_jobs_client_ref",
        "ux_files_job_sha256",
    ):
        if candidate in text:
            return candidate
    return ""


def _replay(
    session: Session,
    *,
    client_id: str,
    idempotency_key: str,
    fingerprint: str,
    settings: Settings,
) -> AcceptedSubmission | None:
    existing = submissions_repo.get_by_idempotency_key(session, client_id, idempotency_key)
    if existing is None:
        return None
    if existing.request_fingerprint != fingerprint:
        raise errors.idempotency_key_reused()
    job = session.get(Job, existing.job_id)
    if job is None:
        raise errors.internal_error()
    return accepted_from_submission(existing, job, settings)


def _upsert_job(
    session: Session,
    *,
    client_id: str,
    job_ref: str | None,
    policy_id: str,
    now: datetime,
) -> Job:
    """Upsert the job, retrying when a concurrent insert wins the ``(client, ref)`` race."""

    if job_ref is None:
        return jobs_repo.create(
            session, client_id=client_id, job_ref=None, policy_id=policy_id, now=now
        )

    last_error: IntegrityError | None = None
    for _ in range(3):
        try:
            with session.begin_nested():
                return jobs_repo.upsert_for_submission(
                    session,
                    client_id=client_id,
                    job_ref=job_ref,
                    policy_id=policy_id,
                    now=now,
                )
        except IntegrityError as exc:
            if _constraint_name(exc) != "ux_jobs_client_ref":
                raise
            last_error = exc
    assert last_error is not None
    raise last_error


def _store_and_insert(
    session: Session,
    *,
    document: Any,
    submission: Submission,
    job: Job,
    client_id: str,
    storage: ObjectStorage,
    inserted_keys: list[str],
    now: datetime,
    max_attempts: int,
) -> bool:
    """Insert one new file row. Returns False when a concurrent insert won the race."""

    file_id = new_id("file_")
    key = source_key(
        client_id=client_id,
        job_id=job.id,
        file_id=file_id,
        extension=document.spooled.extension,
    )
    with document.spooled.temp_path.open("rb") as handle:
        storage.put(key, handle, document.spooled.content_type)
    try:
        with session.begin_nested():
            files_repo.insert(
                session,
                file_id=file_id,
                submission_id=submission.id,
                job_id=job.id,
                client_id=client_id,
                filename=document.spooled.filename,
                content_type=document.spooled.content_type,
                size_bytes=document.spooled.size_bytes,
                sha256=document.spooled.sha256,
                storage_key=key,
                page_count=document.page_count,
                now=now,
            )
    except IntegrityError as exc:
        storage.delete(key)
        if _constraint_name(exc) == "ux_files_job_sha256":
            return False
        raise
    inserted_keys.append(key)
    tasks_repo.enqueue(
        session,
        job_id=job.id,
        submission_id=submission.id,
        kind="prepare_file",
        payload={
            "file_id": file_id,
            "submission_id": submission.id,
            "job_id": job.id,
        },
        dedupe_key=f"prepare_file:{file_id}",
        now=now,
        max_attempts=max_attempts,
    )
    return True


def _resurrect(
    session: Session,
    *,
    file_row: File,
    document: Any,
    submission: Submission,
    storage: ObjectStorage,
    now: datetime,
    max_attempts: int,
) -> None:
    """Re-process a failed or purged file instead of counting it as a duplicate."""

    key = file_row.storage_key
    with document.spooled.temp_path.open("rb") as handle:
        storage.put(key, handle, document.spooled.content_type)
    file_row.submission_id = submission.id
    file_row.filename = document.spooled.filename
    file_row.content_type = document.spooled.content_type
    file_row.size_bytes = document.spooled.size_bytes
    file_row.page_count = document.page_count
    file_row.status = "received"
    file_row.error_code = None
    file_row.error_message = None
    file_row.processed_at = None
    file_row.purged_at = None
    tasks_repo.rearm(
        session,
        dedupe_key=f"prepare_file:{file_row.id}",
        job_id=file_row.job_id,
        submission_id=submission.id,
        kind="prepare_file",
        payload={
            "file_id": file_row.id,
            "submission_id": submission.id,
            "job_id": file_row.job_id,
        },
        now=now,
        max_attempts=max_attempts,
    )


def accept(
    session: Session,
    *,
    client: Client,
    validated: ValidatedSubmission,
    idempotency_key: str | None,
    storage: ObjectStorage,
    settings: Settings,
    clock: Clock,
) -> AcceptedSubmission:
    """Validate-then-accept a fully validated submission.

    Order inside this function matters: idempotent replay is resolved before any row is
    written, then the job is upserted, then duplicates are detected, then object
    storage receives only files that passed every check, then rows and tasks commit.
    """

    now = clock.now()
    hashes = [document.spooled.sha256 for document in validated.documents]
    fingerprint = request_fingerprint(
        job_ref=validated.job_ref,
        webhook_url=validated.webhook_url,
        file_sha256s=hashes,
    )

    if idempotency_key is not None:
        replay = _replay(
            session,
            client_id=client.id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            settings=settings,
        )
        if replay is not None:
            session.rollback()
            return replay

    policy = policies_repo.get_active(session)
    if policy is None:
        session.rollback()
        raise errors.service_unavailable("no active policy is configured")
    policy_id = policy.id
    session.rollback()

    inserted_keys: list[str] = []
    try:
        if idempotency_key is not None:
            # Re-check under the write transaction; another request may have won the race.
            replay = _replay(
                session,
                client_id=client.id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                settings=settings,
            )
            if replay is not None:
                session.rollback()
                return replay

        job = _upsert_job(
            session,
            client_id=client.id,
            job_ref=validated.job_ref,
            policy_id=policy_id,
            now=now,
        )

        existing_by_hash = files_repo.find_by_hashes(session, job.id, hashes)
        duplicate_count = 0
        new_documents = []
        resurrect: list[tuple[File, Any]] = []
        handled_hashes: set[str] = set()
        for document in validated.documents:
            sha256 = document.spooled.sha256
            if sha256 in handled_hashes:
                duplicate_count += 1
                continue
            handled_hashes.add(sha256)
            existing = existing_by_hash.get(sha256)
            if existing is None:
                new_documents.append(document)
            elif existing.status in RETRYABLE_FILE_STATUSES:
                resurrect.append((existing, document))
            else:
                duplicate_count += 1

        submission = submissions_repo.create(
            session,
            job=job,
            client_id=client.id,
            webhook_url=validated.webhook_url,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint if idempotency_key is not None else None,
            document_count=len(validated.documents),
            duplicate_count=duplicate_count,
            now=now,
        )

        for document in new_documents:
            inserted = _store_and_insert(
                session,
                document=document,
                submission=submission,
                job=job,
                client_id=client.id,
                storage=storage,
                inserted_keys=inserted_keys,
                now=now,
                max_attempts=settings.job_max_attempts,
            )
            if not inserted:
                duplicate_count += 1

        for file_row, document in resurrect:
            _resurrect(
                session,
                file_row=file_row,
                document=document,
                submission=submission,
                storage=storage,
                now=now,
                max_attempts=settings.job_max_attempts,
            )

        submission.duplicate_count = duplicate_count

        if not new_documents and not resurrect:
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

        session.commit()
    except IntegrityError as exc:
        session.rollback()
        for key in inserted_keys:
            storage.delete(key)
        constraint = _constraint_name(exc)
        if idempotency_key is not None:
            replay = _replay(
                session,
                client_id=client.id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                settings=settings,
            )
            if replay is not None:
                session.rollback()
                return replay
            if constraint == "ux_submissions_client_idempotency":
                raise errors.idempotency_key_reused() from None
        raise errors.internal_error() from exc
    except BaseException:
        session.rollback()
        for key in inserted_keys:
            storage.delete(key)
        raise

    return accepted_from_submission(submission, job, settings)


def build_result(
    session: Session,
    submission: Submission,
    *,
    include: set[str] | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the envelope-only result view for this submission.

    No document rows exist in this story: ``documents`` and ``conflicts`` are empty
    until segmentation lands. Counts and status still make the async lifecycle
    observable. ``include`` and ``document_id`` are validated and applied now so the
    response shape does not change when documents arrive.
    """

    job = session.get(Job, submission.job_id)
    if job is None:
        raise errors.internal_error()
    policy = session.get(Policy, job.policy_id)
    if policy is None:
        raise errors.service_unavailable("the submission's policy record is missing")
    file_rows = files_repo.list_for_submission(session, submission.id)
    included = include if include is not None else {"documents", "conflicts", "decision"}

    processed_files = [row for row in file_rows if row.status == "processed"]
    failed_files = [row for row in file_rows if row.status == "failed"]
    pages_total = sum(row.page_count or 0 for row in file_rows)
    pages_read = sum(row.page_count or 0 for row in processed_files)
    documents_completed = submission.duplicate_count + len(processed_files)
    documents_failed = len(failed_files)

    decision = None
    if submission.status == "failed":
        reason = submission.error_message or "the submission failed before producing a result"
        decision = {"value": "failed", "route": "none", "reasons": [reason]}

    documents: list[dict[str, Any]] = []
    if document_id is not None:
        documents = [row for row in documents if row.get("document_id") == document_id]

    result: dict[str, Any] = {
        "result_id": submission.result_id,
        "job_id": job.id,
        "job_ref": job.job_ref,
        "status": submission.status,
        "progress": {
            "documents_total": submission.document_count,
            "documents_completed": min(documents_completed, submission.document_count),
            "documents_failed": documents_failed,
            "pages_total": pages_total,
            "pages_read": pages_read,
        },
        "created_at": submission.created_at,
        "completed_at": submission.completed_at,
        "documents": documents if "documents" in included else [],
        "conflicts": [] if "conflicts" in included else [],
        "decision": decision if "decision" in included else None,
        "review": {
            "extraction_open": job.open_review_count,
            "conflict_open": job.open_conflict_count,
        },
        "policy": {
            "version": policy.version,
            "effective_at": policy.activated_at,
        },
    }
    return result
