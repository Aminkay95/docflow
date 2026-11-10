"""Invoice lifecycle adapter. Inference runs with no open database transaction."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings
from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.segment import Segment
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import evidence
from trade_extract.db.repositories.provider_calls import ProviderCallRecorder
from trade_extract.domain.clock import Clock
from trade_extract.domain.conditioning import CoordinateTransform
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import ExtractionOutcome, PageImage
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput, TextCharacter
from trade_extract.providers import make_provider
from trade_extract.schemas.base import EnvelopeSource
from trade_extract.services.checks import run_invoice_checks
from trade_extract.services.decisions import decide_document, effective_policy
from trade_extract.services.extraction import extract
from trade_extract.services.readers.paddle import PaddleReader
from trade_extract.services.segmentation import classify_page
from trade_extract.services.storage import build_storage


def rehydrate(page: dict[str, Any], image_bytes: bytes | None) -> PageInput:
    details = page["conditioning"] or {}
    transform = (
        CoordinateTransform(**{k: tuple(v) for k, v in details["transform"].items()})
        if details.get("transform")
        else None
    )
    return PageInput(
        page_index=page["page_index"],
        image=PageImage(page["page_index"], image_bytes, page["width_px"], page["height_px"])
        if image_bytes is not None
        else None,
        characters=tuple(
            TextCharacter(c["text"], BBox.model_validate(c["bbox"]))
            for c in details.get("characters", [])
        ),
        conditioning_version=page["conditioning_version"],
        conditioning_status=page["status"],
        unreadable_reasons=tuple((page["unreadable_reason"] or "").split(","))
        if page["unreadable_reason"]
        else (),
        transform=transform,
    )


def source_snapshot(document: Any, pages: list[PageInput]) -> dict[str, Any]:
    """Inverse-map reader boxes onto original rendered source pages for API overlays."""
    snapshot = document.model_dump(mode="json")
    transforms = {p.page_index: p.transform for p in pages}
    for field in [*snapshot.get("fields", {}).values(), *snapshot.get("extension", {}).values()]:
        for provenance in [
            field["provenance"],
            *(a["provenance"] for a in field.get("alternatives", [])),
        ]:
            transform = transforms.get(provenance["page_index"])
            if transform is None:
                raise ValueError("missing_source_coordinate_transform")
            provenance["bbox"] = transform.box(
                BBox.model_validate(provenance["bbox"]), inverse=True
            ).model_dump()
    return snapshot


def handle(session: Session, task: JobTask, *, settings: Settings, clock: Clock) -> None:
    task_id, owner, attempt = task.id, task.locked_by, task.attempts
    document = session.get(Document, task.document_id)
    if document is None or document.status not in ("queued", "processing"):
        return
    file = session.get(File, document.file_id)
    job = session.get(Job, document.job_id)
    if file is None or job is None:
        raise ValueError("missing_extraction_identity")
    policy = session.get(Policy, job.policy_id)
    if policy is None:
        raise ValueError("missing_policy")
    policy_id, policy_version, config = policy.id, policy.version, dict(policy.config)
    identity = {
        k: getattr(document, k)
        for k in (
            "id",
            "job_id",
            "file_id",
            "segment_id",
            "client_id",
            "document_type",
            "page_start",
            "page_end",
            "document_hash",
        )
    }
    source = EnvelopeSource(
        filename=file.filename,
        content_type=file.content_type,
        file_hash=f"sha256:{file.sha256}",
        document_hash=f"sha256:{document.document_hash}",
        page_start=document.page_start,
        page_end=document.page_end,
        page_count=document.page_end - document.page_start + 1,
    )
    rows = session.scalars(
        select(Page)
        .where(
            Page.file_id == file.id, Page.page_index.between(document.page_start, document.page_end)
        )
        .order_by(Page.page_index)
    ).all()
    pages = [
        {
            k: getattr(p, k)
            for k in (
                "page_index",
                "image_key",
                "width_px",
                "height_px",
                "conditioning",
                "conditioning_version",
                "status",
                "unreadable_reason",
            )
        }
        for p in rows
    ]
    document.status = "processing"
    document.updated_at = clock.now()
    session.commit()
    if len(pages) != identity["page_end"] - identity["page_start"] + 1:
        raise ValueError("incomplete_segment_pages")
    # All ORM reads are completed before any storage, OCR or provider work.
    storage = build_storage(settings)
    inputs = []
    for page in pages:
        blob = None
        if page["status"] != "unreadable" and page["image_key"]:
            with storage.get(page["image_key"]) as stream:
                blob = stream.read(settings.provider_max_image_bytes + 1)
                if len(blob) > settings.provider_max_image_bytes:
                    raise ValueError("conditioned_image_byte_budget_exhausted")
        inputs.append(rehydrate(page, blob))
    if identity["document_type"] == "unknown":
        paddle = PaddleReader(settings)
        classified_inputs = []
        classifications = []
        for page_input in inputs:
            if page_input.is_unreadable or page_input.image is None:
                classified_inputs.append(page_input)
                continue
            ocr = paddle.inspect(page_input, require_structure=False)
            classified_inputs.append(replace(page_input, ocr=ocr))
            text = "\n".join(span.text for span in ocr.spans if span.text.strip())
            result = classify_page(text, min_confidence=settings.segmentation_min_confidence)
            if result.document_type != "unknown":
                classifications.append(result)
        inputs = classified_inputs
        types = {result.document_type for result in classifications}
        if len(types) == 1:
            reclassified_type = next(iter(types))
            identity["document_type"] = reclassified_type
            identity["document_hash"] = document_hash(
                source_file_sha256=file.sha256,
                page_start=identity["page_start"],
                page_end=identity["page_end"],
                document_type=reclassified_type,
            )
            document.document_type = reclassified_type
            document.document_hash = identity["document_hash"]
            segment = session.get(Segment, identity["segment_id"])
            if segment is not None:
                segment.document_type = reclassified_type
                segment.confidence = min(result.confidence for result in classifications)
            session.commit()
            source = source.model_copy(
                update={"document_hash": f"sha256:{identity['document_hash']}"}
            )
        else:
            outcome = ExtractionOutcome(
                "extracted",
                errors=("unsupported_document_type", "ocr_reclassification_ambiguous"),
                diagnostics=("provider_skipped_unknown_document",),
            )
    if identity["document_type"] != "unknown":
        provider = make_provider(
            settings,
            recorder=ProviderCallRecorder(
                sessionmaker(bind=session.get_bind()),
                provider=settings.provider,
                reader="vision",
                job_id=identity["job_id"],
                document_id=identity["id"],
            ),
        )
        outcome = extract(
            provider,
            document_type=identity["document_type"],
            source=source,
            pages=tuple(inputs),
            segment_id=identity["segment_id"],
            settings=settings,
        )
    status = "failed" if outcome.status == "extraction_failed" else outcome.status
    gate, _ = effective_policy(config)
    checks = (
        run_invoice_checks(outcome.document, tolerance=gate.arithmetic_tolerance)
        if outcome.document and identity["document_type"] == "commercial_invoice"
        else []
    )
    decision = decide_document(
        document_type=identity["document_type"],
        status=status,
        checks=checks,
        policy_id=policy_id,
        policy_version=policy_version,
        config=config,
        has_unreadable_page=any(p.is_unreadable for p in inputs),
    )
    decision["diagnostics"] = list(outcome.errors + outcome.diagnostics)
    decision["routing"] = [asdict(route) for route in outcome.routes]
    # Lock job first: finalization and all evidence writes use the same lock order.
    session.execute(select(Job).where(Job.id == identity["job_id"]).with_for_update()).scalar_one()
    current_task = session.execute(
        select(JobTask)
        .where(JobTask.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if (
        current_task.status != "running"
        or current_task.locked_by != owner
        or current_task.attempts != attempt
    ):
        return
    document = session.execute(
        select(Document)
        .where(Document.id == identity["id"])
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if document.status not in ("queued", "processing"):
        return
    document.extraction = source_snapshot(outcome.document, inputs) if outcome.document else None
    document.status = (
        "review_required" if status == "extracted" and decision["value"] == "review" else status
    )
    document.decision = decision["value"]
    document.decision_reason = "; ".join(decision["reasons"])
    document.extracted_at = clock.now()
    document.updated_at = clock.now()
    segment = session.get(Segment, identity["segment_id"])
    if segment:
        segment.status = "extracted" if outcome.document else "failed"
    snapshot_fields = (document.extraction or {}).get("fields", {})
    supplier = snapshot_fields.get("seller.name", {}).get("value")
    if supplier:
        document.supplier_name = supplier
        document.supplier_key = " ".join(supplier.split()).casefold()
    from trade_extract.services.trust import observe

    observe(session, document, clock.now())
    evidence.save_document(session, document, checks, decision)
    from trade_extract.db.repositories import tasks as tasks_repo

    tasks_repo.rearm(
        session,
        dedupe_key=f"finalize_submission:{document.submission_id}",
        job_id=document.job_id,
        submission_id=document.submission_id,
        kind="finalize_submission",
        payload={"submission_id": document.submission_id, "job_id": document.job_id},
        now=clock.now(),
    )
