"""Tenant-scoped accumulated job reads."""

import json
from copy import deepcopy
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.orm import Session

from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import ConflictRecord, ConflictSource, ReviewItem
from trade_extract.db.models.job import Job
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.review import ReviewResolution
from trade_extract.db.models.submission import Submission
from trade_extract.db.repositories.evidence import conflicts_for
from trade_extract.domain import errors
from trade_extract.services.ingest import build_result


def job_result(session: Session, job_id: str, client_id: str) -> dict[str, Any]:
    """Build a job-wide handoff, retaining source values alongside reviewed values."""
    result = read_job(session, job_id, client_id)
    documents = []
    for record in result["documents"]:
        document = deepcopy(read_document(session, record["document_id"], client_id))
        document["effective_fields"] = {
            **(document.get("fields") or {}),
            **(document.get("extension") or {}),
            **(document.get("corrections") or {}),
        }
        documents.append(document)
    result["documents"] = documents
    result["review_resolutions"] = [
        dict(
            resolution_id=resolution.id,
            reviewer_id=resolution.reviewer_id,
            document_id=item.document_id,
            conflict_id=item.conflict_id,
            field_path=resolution.field_path,
            outcome=resolution.outcome,
            reason=resolution.reason,
            chosen_document_id=resolution.chosen_document_id,
            corrected_value=resolution.corrected_value,
            created_at=resolution.created_at,
        )
        for resolution, item in session.execute(
            select(ReviewResolution, ReviewItem)
            .join(ReviewItem, ReviewItem.id == ReviewResolution.review_item_id)
            .where(ReviewItem.job_id == job_id)
            .order_by(ReviewResolution.created_at, ReviewResolution.id)
        )
    ]
    return jsonable_encoder(result)


def emit_terminal_result(result: dict[str, Any], *, phase: str) -> None:
    """Explicit terminal export requested by the operator; separate from diagnostic logs."""
    print(json.dumps({"event": phase, "result": result}, ensure_ascii=False), flush=True)


def read_job(session: Session, job_id: str, client_id: str) -> dict[str, Any]:
    job = session.scalar(select(Job).where(Job.id == job_id, Job.client_id == client_id))
    if job is None:
        raise errors.job_not_found()
    documents = session.scalars(
        select(Document).where(Document.job_id == job.id).order_by(Document.created_at, Document.id)
    ).all()
    policy = session.get(Policy, job.policy_id)
    if policy is None:
        raise errors.service_unavailable("the job's policy record is missing")
    conflicts = [
        dict(
            conflict_id=c.id,
            canonical_key=c.canonical_key,
            field_label=c.field_label,
            status=c.status,
            source_count=count,
        )
        for c, count in session.execute(
            select(ConflictRecord, func.count(ConflictSource.id))
            .outerjoin(ConflictSource, ConflictSource.conflict_id == ConflictRecord.id)
            .where(ConflictRecord.job_id == job.id, ConflictRecord.client_id == client_id)
            .group_by(ConflictRecord.id)
            .order_by(ConflictRecord.created_at, ConflictRecord.id)
        )
    ]
    return dict(
        job_id=job.id,
        job_ref=job.job_ref,
        status=job.status,
        created_at=job.created_at,
        last_submission_at=job.last_submission_at,
        terminal_at=job.terminal_at,
        document_count=len(documents),
        documents=[
            dict(
                document_id=d.id,
                document_type=d.document_type,
                status=d.status,
                decision=d.decision,
                created_at=d.created_at,
                submitted_at=d.created_at,
            )
            for d in documents
        ],
        conflicts=conflicts,
        review=dict(extraction_open=job.open_review_count, conflict_open=job.open_conflict_count),
        policy=dict(version=policy.version),
    )


def read_document(session: Session, document_id: str, client_id: str) -> dict[str, Any]:
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.client_id == client_id)
    )
    if document is None:
        raise errors.DomainError("document_not_found", "Document not found.", http_status=404)
    submission = session.get(Submission, document.submission_id)
    if submission is None:
        raise errors.result_not_found()
    return build_result(session, submission, document_id=document_id)["documents"][0]


def read_collection(
    session: Session,
    job_id: str,
    client_id: str,
    *,
    collection: str,
    limit: int,
    cursor: str | None,
    status: str | None,
    document_type: str | None,
) -> dict[str, Any]:
    if session.scalar(select(Job.id).where(Job.id == job_id, Job.client_id == client_id)) is None:
        raise errors.job_not_found()
    query: Select[Any]
    if collection == "documents":
        query = select(Document).where(Document.job_id == job_id, Document.client_id == client_id)
        if status is not None:
            query = query.where(Document.status == status)
        if document_type is not None:
            query = query.where(Document.document_type == document_type)
        created, identity = Document.created_at, Document.id
    else:
        query = select(ConflictRecord).where(
            ConflictRecord.job_id == job_id, ConflictRecord.client_id == client_id
        )
        if status is not None:
            query = query.where(ConflictRecord.status == status)
        if document_type is not None:
            query = query.where(
                ConflictRecord.id.in_(
                    select(ConflictSource.conflict_id)
                    .join(Document, Document.id == ConflictSource.document_id)
                    .where(Document.document_type == document_type, Document.client_id == client_id)
                )
            )
        created, identity = ConflictRecord.created_at, ConflictRecord.id
    if cursor:
        previous = session.scalar(query.where(identity == cursor))
        if previous is None:
            raise errors.invalid_request("cursor", "must identify a row in this collection")
        query = query.where(tuple_(created, identity) > tuple_(previous.created_at, previous.id))
    rows = list(session.scalars(query.order_by(created, identity).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    if collection == "documents":
        data = [
            dict(
                document_id=d.id,
                document_type=d.document_type,
                status=d.status,
                decision=d.decision,
                created_at=d.created_at,
                submitted_at=d.created_at,
            )
            for d in rows
        ]
    else:
        by_id = {
            c["conflict_id"]: c
            for c in conflicts_for(
                session, job_id=job_id, client_id=client_id, conflict_ids=[c.id for c in rows]
            )
        }
        data = [{**by_id[c.id], "source_count": len(by_id[c.id]["sources"])} for c in rows]
    return dict(data=data, has_more=more, next_cursor=rows[-1].id if more else None)
