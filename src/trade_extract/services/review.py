"""Transactional human resolutions with job-first locking."""

from copy import deepcopy
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import ConflictRecord, ConflictSource, ReviewItem
from trade_extract.db.models.job import Job
from trade_extract.db.models.review import ReviewResolution
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.models.submission import Submission
from trade_extract.db.repositories.evidence import review_counts
from trade_extract.domain.errors import DomainError, invalid_request
from trade_extract.domain.ids import new_id
from trade_extract.services.trust import demote


def resolve(
    session: Session,
    *,
    item_id: str,
    client_id: str,
    reviewer: Reviewer,
    outcome: str,
    reason: str,
    now: datetime,
    field_path: str | None = None,
    corrected_value: dict[str, Any] | None = None,
    chosen_document_id: str | None = None,
    expected_fingerprint: str | None = None,
) -> ReviewResolution:
    if reviewer.role not in ("reviewer", "admin") or not reviewer.is_active:
        raise DomainError("forbidden", "Read-only account.", http_status=403)
    if len(reason.strip()) < 4:
        raise invalid_request("reason", "must contain at least four characters")
    if outcome != "corrected" and corrected_value is not None:
        raise invalid_request("corrected_value", "is allowed only for a correction")
    candidate = session.get(ReviewItem, item_id)
    if candidate is None:
        raise DomainError("review_not_found", "Review item not found.", http_status=404)
    job = session.scalar(
        select(Job)
        .where(Job.id == candidate.job_id, Job.client_id == client_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise DomainError("review_not_found", "Review item not found.", http_status=404)
    item = session.scalars(
        select(ReviewItem)
        .where(ReviewItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if item.status not in ("open", "in_progress") or item.assigned_to not in (None, reviewer.id):
        raise DomainError("review_stale", "Review item changed; reload it.", http_status=409)
    prior: dict[str, Any] | None = None
    if item.track == "extraction":
        if chosen_document_id is not None:
            raise invalid_request("chosen_document_id", "is allowed only for conflict acceptance")
        document = session.get_one(Document, item.document_id)
        fields = {
            **(document.extraction or {}).get("fields", {}),
            **(document.extraction or {}).get("extension", {}),
        }
        if outcome == "unreadable_confirmed":
            if document.status != "unreadable":
                raise invalid_request("outcome", "document must be unreadable")
            field_path = "$"
            prior = {"status": document.status}
        elif outcome == "failure_confirmed":
            if fields or document.status not in ("failed", "review_required"):
                raise invalid_request(
                    "outcome", "requires a failed or unsupported empty extraction"
                )
            field_path = "$"
            prior = {"status": document.status, "reason": document.decision_reason}
        elif outcome in ("confirmed", "corrected"):
            if field_path not in fields:
                raise invalid_request("field_path", "must name an extracted field")
            if session.scalar(
                select(ReviewResolution.id)
                .where(
                    ReviewResolution.review_item_id == item.id,
                    ReviewResolution.field_path == field_path,
                )
                .limit(1)
            ):
                raise DomainError(
                    "review_stale", "Field already resolved; reload.", http_status=409
                )
            prior = fields[field_path]
            if outcome == "corrected":
                from trade_extract.domain.fields import Field

                if corrected_value is None:
                    raise invalid_request("corrected_value", "is required")
                corrected_value = {
                    **corrected_value,
                    "agreement": "human_corrected",
                    "alternatives": [],
                }
                try:
                    Field.model_validate(corrected_value)
                    snapshot = deepcopy(document.extraction or {})
                    section = "fields" if field_path in snapshot.get("fields", {}) else "extension"
                    snapshot[section][field_path] = corrected_value
                    from trade_extract.schemas.registry import DOCUMENT_SCHEMAS

                    DOCUMENT_SCHEMAS[document.document_type].model.model_validate(snapshot)
                except (ValidationError, KeyError) as exc:
                    raise invalid_request(
                        "corrected_value", "must satisfy the document schema"
                    ) from exc
                if corrected_value["provenance"] != prior["provenance"]:
                    raise invalid_request("provenance", "must preserve source evidence")
                demote(session, document, now)
        else:
            raise invalid_request(
                "outcome", "must be confirmed, corrected, unreadable_confirmed or failure_confirmed"
            )
    else:
        conflict = session.get_one(ConflictRecord, item.conflict_id)
        if conflict.status != "open":
            raise DomainError("review_stale", "Conflict already resolved; reload.", http_status=409)
        if expected_fingerprint != conflict.fingerprint:
            raise DomainError("review_stale", "Conflict evidence changed; reload.", http_status=409)
        if field_path is not None:
            raise invalid_request("field_path", "is set by the conflict evidence")
        if chosen_document_id is not None and outcome != "accepted":
            raise invalid_request("chosen_document_id", "is allowed only for acceptance")
        if outcome not in ("accepted", "waived", "client_notified"):
            raise invalid_request("outcome", "must be accepted, waived or client_notified")
        if (
            outcome == "accepted"
            and session.scalar(
                select(ConflictSource.id).where(
                    ConflictSource.conflict_id == conflict.id,
                    ConflictSource.document_id == chosen_document_id,
                )
            )
            is None
        ):
            raise invalid_request("chosen_document_id", "must name a conflict source")
        field_path = conflict.canonical_key
        prior = {
            "status": conflict.status,
            "sources": list(
                session.scalars(
                    select(ConflictSource.evidence).where(ConflictSource.conflict_id == conflict.id)
                )
            ),
        }
        conflict.status = {"accepted": "resolved", "waived": "waived", "client_notified": "open"}[
            outcome
        ]
        conflict.updated_at = now
        if outcome != "accepted":
            chosen_document_id = None
    label = ReviewResolution(
        id=new_id("rr_"),
        review_item_id=item.id,
        reviewer_id=reviewer.id,
        field_path=field_path,
        prior_value=prior,
        corrected_value=corrected_value,
        outcome=outcome,
        reason=reason.strip(),
        chosen_document_id=chosen_document_id,
        created_at=now,
    )
    session.add(label)
    session.flush()
    close = outcome != "client_notified"
    if item.track == "extraction" and outcome not in ("unreadable_confirmed", "failure_confirmed"):
        reviewed = set(
            session.scalars(
                select(ReviewResolution.field_path).where(
                    ReviewResolution.review_item_id == item.id,
                    ReviewResolution.outcome.in_(("confirmed", "corrected")),
                )
            )
        )
        close = set(fields) <= reviewed
    if close:
        item.status = "resolved"
        if item.track == "extraction":
            document.status = "resolved"
            document.resolved_at = now
    item.updated_at = now
    job.open_review_count, job.open_conflict_count = review_counts(session, job.id)
    pending = session.scalar(
        select(Submission.id)
        .where(Submission.job_id == job.id, Submission.status.in_(("queued", "processing")))
        .limit(1)
    )
    if pending is None and job.status != "failed":
        job.status = "review_required" if sum(review_counts(session, job.id)) else "completed"
        job.terminal_at = now if job.status == "completed" else None
        job.terminal_reason = "completed" if job.terminal_at else None
    job.updated_at = now
    session.flush()
    return label
