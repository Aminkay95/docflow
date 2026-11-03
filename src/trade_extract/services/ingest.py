from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.segment import Segment
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import documents as documents_repo
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
            # Reuse finalization's retained review/conflict decision. No new extraction
            # or repeat-call accumulation behavior is introduced for duplicate files.
            from trade_extract.workers.handlers.finalize_submission import handle

            transient = JobTask(
                id=new_id("task_"),
                job_id=job.id,
                submission_id=submission.id,
                kind="finalize_submission",
                payload={"submission_id": submission.id},
                attempts=0,
            )
            active_documents = (
                session.query(Document)
                .filter(Document.job_id == job.id, Document.status.in_(("queued", "processing")))
                .first()
            )
            if active_documents is not None:
                tasks_repo.enqueue(
                    session,
                    job_id=job.id,
                    submission_id=submission.id,
                    kind="finalize_submission",
                    payload={"submission_id": submission.id},
                    now=now,
                    dedupe_key=f"finalize_submission:{submission.id}",
                )
            else:
                handle(session, transient, settings=settings, clock=clock)

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


def _document_object(
    document: Document,
    segment: Segment,
    file_row: File,
    *,
    submission: Submission,
    job: Job,
) -> dict[str, Any]:
    """One document envelope for the polling result.

    Extraction has not run in this slice: fields, tables, extension, checks and the
    decision stay null. Hashes are stored bare and rendered with ``sha256:``.
    """

    return {
        "document_id": document.id,
        "result_id": submission.result_id,
        "job_id": job.id,
        "job_ref": job.job_ref,
        "file_id": file_row.id,
        "segment_id": segment.id,
        "document_type": document.document_type,
        "document_type_confidence": float(segment.confidence),
        "schema_version": None,
        "status": document.status,
        "source": {
            "filename": file_row.filename,
            "content_type": file_row.content_type,
            "file_hash": f"sha256:{file_row.sha256}",
            "document_hash": f"sha256:{document.document_hash}",
            "page_start": document.page_start,
            "page_end": document.page_end,
            "page_count": document.page_end - document.page_start + 1,
        },
        "fields": None,
        "tables": {},
        "extension": {},
        "checks": None,
        "conflicts": [],
        "decision": None,
        "review": {"extraction_open": False, "conflict_open": False},
        "created_at": document.created_at,
        "extracted_at": document.extracted_at,
    }


def build_result(
    session: Session,
    submission: Submission,
    *,
    include: set[str] | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the result view for this submission.

    Documents are joined from persisted document, segment and file rows. When no
    document rows exist (a duplicate-only submission, or a failure before
    preparation) the story-1 file-count semantics stay.
    """

    job = session.get(Job, submission.job_id)
    if job is None:
        raise errors.internal_error()
    policy = session.get(Policy, job.policy_id)
    if policy is None:
        raise errors.service_unavailable("the submission's policy record is missing")
    file_rows = files_repo.list_for_submission(session, submission.id)
    included = include if include is not None else {"documents", "conflicts", "decision"}

    document_rows = documents_repo.list_for_submission(session, submission.id)
    processed_files = [row for row in file_rows if row.status == "processed"]
    failed_files = [row for row in file_rows if row.status == "failed"]
    pages_total = sum(row.page_count or 0 for row in file_rows)
    pages_read = sum(row.page_count or 0 for row in processed_files)

    if document_rows:
        documented_file_ids = {file_row.id for _, _, file_row in document_rows}
        pending_without_documents = len(
            [
                row
                for row in file_rows
                if row.status in ("received", "processing") and row.id not in documented_file_ids
            ]
        )
        failed_without_documents = len(
            [row for row in failed_files if row.id not in documented_file_ids]
        )
        duplicates = submission.duplicate_count
        documents_total = (
            len(document_rows) + pending_without_documents + failed_without_documents + duplicates
        )
        documents_completed = (
            sum(
                1
                for document, _, _ in document_rows
                if document.status in ("extracted", "review_required", "resolved")
            )
            + duplicates
        )
        documents_failed = (
            sum(
                1 for document, _, _ in document_rows if document.status in ("failed", "unreadable")
            )
            + failed_without_documents
        )
    else:
        documents_total = submission.document_count
        documents_completed = submission.duplicate_count + len(processed_files)
        documents_failed = len(failed_files)

    decision: dict[str, Any] | None = None
    if submission.status == "failed":
        from trade_extract.domain.checks import CHECK_VERSION
        from trade_extract.services.decisions import effective_policy

        gate, valid_policy = effective_policy(policy.config)
        reason = submission.error_message or "the submission failed before producing a result"
        decision = {
            "value": "failed",
            "route": "none",
            "reasons": [reason],
            "policy_id": policy.id,
            "policy_version": policy.version,
            "policy_snapshot": gate.model_dump(mode="json"),
            "policy_valid": valid_policy,
            "check_version": CHECK_VERSION,
        }

    documents = [
        _document_object(document, segment, file_row, submission=submission, job=job)
        for document, segment, file_row in document_rows
    ]
    from trade_extract.db.models.evidence import DecisionRecord
    from trade_extract.db.repositories import evidence

    conflicts = evidence.conflicts_for(session, job_id=job.id, client_id=submission.client_id)
    recorded_job = session.get(DecisionRecord, f"dec_{submission.id}")
    if recorded_job is not None:
        decision = recorded_job.outcome
    if (
        decision is not None
        and session.query(Document.id)
        .filter(Document.job_id == job.id, Document.status == "failed")
        .first()
        is not None
    ):
        decision = {
            **decision,
            "value": "failed",
            "reasons": sorted(set([*decision["reasons"], "extraction_failed"])),
        }
    current_conflicts = [c for c in conflicts if c["status"] == "open"]
    if current_conflicts and decision is not None:
        decision = {
            **decision,
            "value": "failed" if decision["value"] == "failed" else "review",
            "route": "conflict",
            "reasons": sorted(
                set(
                    decision["reasons"]
                    + [f"conflict_open:{c['canonical_key']}" for c in current_conflicts]
                )
            ),
        }
    document_checks, document_decisions = evidence.document_evidence(
        session, [record.id for record, _, _ in document_rows]
    )
    for output, (record, _, _) in zip(documents, document_rows, strict=True):
        snapshot = record.extraction or {}
        for key in ("schema_version", "fields", "tables", "extension"):
            if key in snapshot:
                output[key] = snapshot[key]
        output["checks"] = document_checks.get(record.id, [])
        output["decision"] = document_decisions.get(record.id)
        output["conflicts"] = [
            c for c in conflicts if any(s["document_id"] == record.id for s in c["sources"])
        ]
        open_document_conflicts = [c for c in output["conflicts"] if c["status"] == "open"]
        output["review"] = {
            "extraction_open": record.decision in ("review", "failed", "unreadable"),
            "conflict_open": bool(open_document_conflicts),
        }
        if open_document_conflicts and output["decision"]:
            output["decision"] = {
                **output["decision"],
                "value": output["decision"]["value"]
                if output["decision"]["value"] in ("failed", "unreadable")
                else "review",
                "route": "conflict",
                "reasons": sorted(
                    set(
                        output["decision"]["reasons"]
                        + [f"conflict_open:{c['canonical_key']}" for c in open_document_conflicts]
                    )
                ),
            }
    if document_id is not None:
        documents = [row for row in documents if row.get("document_id") == document_id]

    result: dict[str, Any] = {
        "result_id": submission.result_id,
        "job_id": job.id,
        "job_ref": job.job_ref,
        "status": submission.status,
        "progress": {
            "documents_total": documents_total,
            "documents_completed": min(documents_completed, documents_total),
            "documents_failed": documents_failed,
            "pages_total": pages_total,
            "pages_read": pages_read,
        },
        "created_at": submission.created_at,
        "completed_at": submission.completed_at,
        "documents": documents if "documents" in included else [],
        "conflicts": conflicts if "conflicts" in included else [],
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
