from __future__ import annotations

import io
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from trade_extract.config import Settings
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import documents as documents_repo
from trade_extract.db.repositories import files as files_repo
from trade_extract.db.repositories import jobs as jobs_repo
from trade_extract.db.repositories import pages as pages_repo
from trade_extract.db.repositories import segments as segments_repo
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.db.repositories import tasks as tasks_repo
from trade_extract.domain import errors
from trade_extract.domain.clock import Clock
from trade_extract.domain.hashing import document_hash, document_idempotency_key
from trade_extract.domain.ids import new_id
from trade_extract.services.conditioning import condition_page, diagnostics
from trade_extract.services.page_model import IMAGE_CONTENT_TYPE, render_container
from trade_extract.services.segmentation import SegmentSpec, segment_pages
from trade_extract.services.storage import (
    ObjectStorage,
    build_storage,
    page_image_key,
    page_thumbnail_key,
)


@dataclass(frozen=True)
class _StoredPage:
    page_index: int
    width_px: int
    height_px: int
    dpi: int
    rotation_deg: float
    text: str
    image_key: str
    thumbnail_key: str
    conditioning: dict[str, Any]
    conditioning_version: str
    status: str
    unreadable_reason: str | None
    legibility_score: float


def _download_source(storage: ObjectStorage, file_row: File) -> Path:
    """Stream the stored source into a temp file the renderer can open."""

    suffix = Path(file_row.storage_key).suffix or ".bin"
    handle, temp_name = tempfile.mkstemp(prefix="trade-extract-prepare-", suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as target:
            source = storage.get(file_row.storage_key)
            try:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            finally:
                source.close()
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return Path(temp_name)


def _render_and_store(
    storage: ObjectStorage,
    file_row: File,
    *,
    settings: Settings,
    temp_path: Path,
) -> list[_StoredPage]:
    stored: list[_StoredPage] = []
    for rendering in render_container(
        temp_path,
        file_row.content_type,
        document=file_row.filename,
        dpi=settings.page_render_dpi,
        thumbnail_max_px=settings.page_thumbnail_max_px,
        quality=settings.page_image_quality,
        max_pixels=settings.conditioning_max_pixels,
    ):
        result = condition_page(rendering, settings)
        image_key = page_image_key(
            client_id=file_row.client_id,
            job_id=file_row.job_id,
            file_id=file_row.id,
            page_index=rendering.page_index,
        )
        thumbnail_key = page_thumbnail_key(
            client_id=file_row.client_id,
            job_id=file_row.job_id,
            file_id=file_row.id,
            page_index=rendering.page_index,
        )
        # Images land before the row-writing transaction: a crash after this point
        # leaves at most orphan objects, never a half-written page set.
        storage.put(image_key, io.BytesIO(result.image_bytes), IMAGE_CONTENT_TYPE)
        storage.put(thumbnail_key, io.BytesIO(result.thumbnail_bytes), IMAGE_CONTENT_TYPE)
        stored.append(
            _StoredPage(
                page_index=rendering.page_index,
                width_px=result.transform.conditioned_size[0],
                height_px=result.transform.conditioned_size[1],
                dpi=result.dpi,
                rotation_deg=rendering.rotation_deg,
                text=rendering.text,
                image_key=image_key,
                thumbnail_key=thumbnail_key,
                conditioning=diagnostics(result, rendering),
                conditioning_version=result.version,
                status=result.status,
                unreadable_reason=",".join(result.reasons) or None,
                legibility_score=result.score,
            )
        )
    return stored


def handle(
    session: Session,
    task: JobTask,
    *,
    settings: Settings,
    clock: Clock,
) -> None:
    """Prepare one file: pages, always-on segmentation and one document per segment.

    The file row is locked while it is prepared. Re-running for a file whose page,
    segment and document set already exists is a no-op, so a re-armed prepare task
    cannot duplicate documents. A failed or purged file is re-processed by ingest
    with a fresh submission; the prior set is replaced in one transaction.
    """

    now = clock.now()
    payload = task.payload or {}
    file_id = payload.get("file_id")
    if not file_id:
        return

    file_row = files_repo.get_for_update(session, file_id)
    if file_row is None:
        return
    if file_row.status == "processed" and documents_repo.exists_for_file(session, file_id):
        # RETRY_AFTER_SUCCESS: the page/segment/document set is already complete.
        # A resurrected file has status ``received`` even though the old set still
        # exists, so it falls through and replaces that set below.
        return

    submission = session.get(Submission, file_row.submission_id)
    if submission is None or submission.status in ("failed", "completed"):
        return
    job = session.get(Job, file_row.job_id)
    if job is None:
        return

    files_repo.mark_processing(session, file_row)
    submissions_repo.mark_processing(session, submission)
    jobs_repo.mark_processing(session, job, now=now)

    storage = build_storage(settings)
    temp_path: Path | None = None
    try:
        temp_path = _download_source(storage, file_row)
        stored_pages = _render_and_store(storage, file_row, settings=settings, temp_path=temp_path)
    except errors.DomainError:
        raise
    except Exception as exc:  # noqa: BLE001 - storage and render errors are worker failures
        raise errors.processing_failed(
            file_row.filename, "the source could not be read, rendered or stored"
        ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if not stored_pages:
        raise errors.processing_failed(file_row.filename, "the container produced no pages")

    try:
        specs = segment_pages(
            [page.text for page in stored_pages],
            min_confidence=settings.segmentation_min_confidence,
        )
    except Exception as exc:  # noqa: BLE001 - deterministic segmentation guard
        raise errors.processing_failed(
            file_row.filename, "the pages could not be segmented"
        ) from exc

    # One transaction: replace any prior set, then write pages, segments and one
    # document per segment. The runner commits once the handler returns.
    documents_repo.delete_for_file(session, file_row.id)
    segments_repo.delete_for_file(session, file_row.id)
    pages_repo.delete_for_file(session, file_row.id)

    for page in stored_pages:
        pages_repo.insert(
            session,
            page_id=new_id("pg_"),
            file_id=file_row.id,
            page_index=page.page_index,
            width_px=page.width_px,
            height_px=page.height_px,
            dpi=page.dpi,
            rotation_deg=page.rotation_deg,
            text=page.text,
            image_key=page.image_key,
            thumbnail_key=page.thumbnail_key,
            now=now,
            conditioning=page.conditioning,
            conditioning_version=page.conditioning_version,
            status=page.status,
            unreadable_reason=page.unreadable_reason,
            legibility_score=page.legibility_score,
        )

    inserted_segments: list[tuple[SegmentSpec, str]] = []
    for spec in specs:
        segment_id = new_id("seg_")
        segments_repo.insert(
            session,
            segment_id=segment_id,
            file_id=file_row.id,
            job_id=file_row.job_id,
            page_start=spec.page_start,
            page_end=spec.page_end,
            document_type=spec.document_type,
            confidence=spec.confidence,
            fingerprint=spec.fingerprint,
            now=now,
        )
        inserted_segments.append((spec, segment_id))
    # Documents reference segments by id; flush them in dependency order without
    # relying on ORM relationship ordering.
    session.flush()

    for spec, segment_id in inserted_segments:
        document = documents_repo.create_for_segment(
            session,
            document_id=new_id("doc_"),
            job_id=file_row.job_id,
            submission_id=file_row.submission_id,
            file_id=file_row.id,
            segment_id=segment_id,
            client_id=file_row.client_id,
            document_type=spec.document_type,
            source_file_hash=file_row.sha256,
            document_hash=document_hash(
                source_file_sha256=file_row.sha256,
                page_start=spec.page_start,
                page_end=spec.page_end,
                document_type=spec.document_type,
            ),
            idempotency_key=document_idempotency_key(
                source_file_sha256=file_row.sha256,
                job_ref=job.job_ref,
                page_start=spec.page_start,
                page_end=spec.page_end,
                document_type=spec.document_type,
            ),
            page_start=spec.page_start,
            page_end=spec.page_end,
            template_fingerprint=spec.fingerprint,
            now=now,
        )
        session.flush()
        if spec.document_type == "commercial_invoice":
            tasks_repo.enqueue(
                session,
                job_id=file_row.job_id,
                submission_id=file_row.submission_id,
                document_id=document.id,
                segment_id=segment_id,
                kind="extract_segment",
                now=now,
                dedupe_key=f"extract_segment:{document.id}",
            )
        else:
            from trade_extract.db.models.policy import Policy
            from trade_extract.db.repositories.evidence import save_document
            from trade_extract.services.decisions import decide_document

            policy = session.get(Policy, job.policy_id)
            if policy is None:
                raise ValueError("missing_policy")
            decision = decide_document(
                document_type=spec.document_type,
                status="extracted",
                checks=[],
                policy_id=policy.id,
                policy_version=policy.version,
                config=policy.config,
            )
            decision["diagnostics"] = ["document_type_not_activated"]
            document.status = "review_required"
            document.decision = "review"
            document.decision_reason = "; ".join(decision["reasons"])
            save_document(session, document, [], decision)

    files_repo.mark_processed(session, file_row, now=now)

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
