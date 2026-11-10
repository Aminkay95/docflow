"""Stage-01 handler: LLM classify a segment, then enqueue stage-02 extraction
with the matching type contract. Only runs when the pipeline trigger fires.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings
from trade_extract.db.models.document import Document
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.segment import Segment
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import tasks as tasks_repo
from trade_extract.db.repositories.evidence import save_document
from trade_extract.db.repositories.provider_calls import ProviderCallRecorder
from trade_extract.domain.clock import Clock
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.inference import ProviderError
from trade_extract.providers import make_provider
from trade_extract.services.decisions import decide_document
from trade_extract.services.stages import classify_segment
from trade_extract.services.trust import observe


def handle(session: Session, task: JobTask, *, settings: Settings, clock: Clock) -> None:
    now = clock.now()
    document = session.get(Document, task.document_id)
    if document is None or document.status not in ("queued", "processing"):
        return
    segment_row = session.get(Segment, document.segment_id)
    if segment_row is None:
        raise ValueError("missing_classify_segment")
    job = session.get(Job, document.job_id)
    if job is None:
        raise ValueError("missing_classify_job")
    policy = session.get(Policy, job.policy_id)
    if policy is None:
        raise ValueError("missing_policy")

    # Lock the document before the provider call so re-arms stay idempotent.
    document.status = "processing"
    document.updated_at = now
    session.commit()

    pages = session.scalars(
        select(Page)
        .where(
            Page.file_id == document.file_id,
            Page.page_index.between(document.page_start, document.page_end),
        )
        .order_by(Page.page_index)
    ).all()
    evidence = "\n".join((p.text_layer or "") for p in pages)
    evidence = evidence[: settings.provider_max_evidence_characters_per_page * max(1, len(pages))]
    if not evidence.strip():
        # No text layer: nothing for text_reason to classify — never burn a call on empty input.
        document.status = "review_required"
        document.decision = "review"
        document.decision_reason = "classification_no_text_layer"
        document.updated_at = now
        observe(session, document, now)
        save_document(
            session, document, [], {"value": "review", "reasons": [document.decision_reason]}
        )
        tasks_repo.rearm(
            session,
            dedupe_key=f"finalize_submission:{document.submission_id}",
            job_id=document.job_id,
            submission_id=document.submission_id,
            kind="finalize_submission",
            payload={"submission_id": document.submission_id, "job_id": document.job_id},
            now=now,
        )
        session.commit()
        return

    provider = make_provider(
        settings,
        recorder=ProviderCallRecorder(
            sessionmaker(bind=session.get_bind()),
            provider=settings.provider,
            reader="text_layer",
            job_id=document.job_id,
            document_id=document.id,
        ),
    )

    verdict = None
    try:
        verdict = classify_segment(
            provider,
            evidence=evidence,
            source_hash=document.source_file_hash,
            segment_id=document.segment_id,
            settings=settings,
        )
    except (ProviderError, ValueError, json.JSONDecodeError) as exc:
        document.status = "review_required"
        document.decision = "review"
        document.decision_reason = f"classification_failed:{type(exc).__name__}"
        document.updated_at = now
        observe(session, document, now)
        save_document(
            session, document, [], {"value": "review", "reasons": [document.decision_reason]}
        )
        tasks_repo.rearm(
            session,
            dedupe_key=f"finalize_submission:{document.submission_id}",
            job_id=document.job_id,
            submission_id=document.submission_id,
            kind="finalize_submission",
            payload={"submission_id": document.submission_id, "job_id": document.job_id},
            now=now,
        )
        session.commit()
        return

    assert verdict is not None

    if verdict.document_type == "unknown":
        # Low-confidence or unknown: human review, never guess a type to keep the pipeline moving.
        decision = decide_document(
            document_type="unknown",
            status="extracted",
            checks=[],
            policy_id=policy.id,
            policy_version=policy.version,
            config=dict(policy.config),
        )
        decision["diagnostics"] = [f"llm_classify_unknown:{verdict.confidence:.4f}"]
        document.document_type = "unknown"
        document.document_hash = document_hash(
            source_file_sha256=document.source_file_hash,
            page_start=document.page_start,
            page_end=document.page_end,
            document_type="unknown",
        )
        document.status = "review_required"
        document.decision = "review"
        document.decision_reason = "; ".join(decision["reasons"])
        document.updated_at = now
        observe(session, document, now)
        save_document(session, document, [], decision)
        segment = session.get(Segment, document.segment_id)
        if segment is not None:
            segment.document_type = "unknown"
            segment.confidence = verdict.confidence
            segment.status = "failed"
        session.commit()
    else:
        # Confident registered type: persist verdict, then enqueue extraction.
        document.document_type = verdict.document_type
        document.document_hash = document_hash(
            source_file_sha256=document.source_file_hash,
            page_start=document.page_start,
            page_end=document.page_end,
            document_type=verdict.document_type,
        )
        document.status = "queued"
        document.updated_at = now
        segment = session.get(Segment, document.segment_id)
        if segment is not None:
            segment.document_type = verdict.document_type
            segment.confidence = verdict.confidence
            segment.status = "pending"
        session.flush()
        tasks_repo.enqueue(
            session,
            job_id=document.job_id,
            submission_id=document.submission_id,
            document_id=document.id,
            segment_id=document.segment_id,
            kind="extract_segment",
            now=now,
            dedupe_key=f"extract_segment:{document.id}",
        )
        session.commit()

    tasks_repo.rearm(
        session,
        dedupe_key=f"finalize_submission:{document.submission_id}",
        job_id=document.job_id,
        submission_id=document.submission_id,
        kind="finalize_submission",
        payload={"submission_id": document.submission_id, "job_id": document.job_id},
        now=now,
    )
    session.commit()
