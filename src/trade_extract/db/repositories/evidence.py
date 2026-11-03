"""Atomic, retry-safe evidence writes under the caller's job lock."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import (
    Check,
    ConflictRecord,
    ConflictSource,
    DecisionRecord,
    ReviewItem,
)
from trade_extract.db.models.job import Job
from trade_extract.domain.canonical import ConflictDraft
from trade_extract.domain.checks import CheckResult
from trade_extract.domain.ids import new_id


def review_counts(session: Session, job_id: str) -> tuple[int, int]:
    session.flush()
    counts = dict(
        session.execute(
            select(ReviewItem.track, func.count())
            .where(ReviewItem.job_id == job_id, ReviewItem.status.in_(("open", "in_progress")))
            .group_by(ReviewItem.track)
        ).all()
    )
    return counts.get("extraction", 0), counts.get("conflict", 0)


def save_document(
    session: Session, document: Document, checks: list[CheckResult], decision: dict[str, Any]
) -> None:
    if decision["value"] != "pass":
        session.add(
            ReviewItem(
                id=new_id("rev_"),
                job_id=document.job_id,
                document_id=document.id,
                conflict_id=None,
                track="extraction",
                status="open",
                reason_code=decision["reasons"][0] if decision["reasons"] else "review_required",
                summary="; ".join(decision["reasons"]),
            )
        )
        session.execute(
            update(Job)
            .where(Job.id == document.job_id)
            .values(open_review_count=Job.open_review_count + 1)
        )
    for check in checks:
        session.add(
            Check(
                id=new_id("chk_"),
                document_id=document.id,
                code=check.code,
                field_path=check.field_path or "$",
                check_version=check.check_version,
                outcome=check.model_dump(mode="json"),
            )
        )
    session.add(
        DecisionRecord(
            id=new_id("dec_"),
            job_id=document.job_id,
            document_id=document.id,
            policy_id=decision["policy_id"],
            outcome=decision,
        )
    )


def checks_for(session: Session, document_id: str) -> list[dict[str, Any]]:
    return list(
        session.scalars(
            select(Check.outcome)
            .where(Check.document_id == document_id)
            .order_by(Check.code, Check.field_path)
        )
    )


def decision_for(session: Session, document_id: str) -> dict[str, Any] | None:
    return session.scalar(
        select(DecisionRecord.outcome).where(DecisionRecord.document_id == document_id)
    )


def document_evidence(
    session: Session, document_ids: list[str]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    checks: dict[str, list[dict[str, Any]]] = {i: [] for i in document_ids}
    decisions: dict[str, dict[str, Any]] = {}
    if document_ids:
        for row in session.scalars(
            select(Check)
            .where(Check.document_id.in_(document_ids))
            .order_by(Check.code, Check.field_path)
        ):
            checks[row.document_id].append(row.outcome)
        for decision in session.scalars(
            select(DecisionRecord).where(DecisionRecord.document_id.in_(document_ids))
        ):
            if decision.document_id is not None:
                decisions[decision.document_id] = decision.outcome
    return checks, decisions


def save_conflicts(
    session: Session, *, job_id: str, client_id: str, drafts: list[ConflictDraft], now: datetime
) -> None:
    for draft in drafts:
        conflict = session.scalar(
            select(ConflictRecord).where(
                ConflictRecord.job_id == job_id,
                ConflictRecord.client_id == client_id,
                ConflictRecord.canonical_key == draft.canonical_key,
                ConflictRecord.status == "open",
            )
        )
        if conflict is None:
            conflict = ConflictRecord(
                id=new_id("cfl_"),
                job_id=job_id,
                client_id=client_id,
                canonical_key=draft.canonical_key,
                field_label=draft.field_label,
                fingerprint=draft.fingerprint,
                status="open",
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            session.add(conflict)
            session.flush()
            session.add(
                ReviewItem(
                    id=new_id("rev_"),
                    job_id=job_id,
                    document_id=None,
                    conflict_id=conflict.id,
                    track="conflict",
                    status="open",
                    reason_code="conflict_open",
                    summary=f"Source documents disagree on {draft.canonical_key}",
                )
            )
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(open_conflict_count=Job.open_conflict_count + 1)
            )
        conflict.last_seen_at = now
        conflict.updated_at = now
        existing = set(
            session.execute(
                select(ConflictSource.document_id, ConflictSource.field_path).where(
                    ConflictSource.conflict_id == conflict.id
                )
            ).all()
        )
        for source in draft.sources:
            if (source["document_id"], source["field_path"]) not in existing:
                session.add(
                    ConflictSource(
                        id=new_id("cfs_"),
                        conflict_id=conflict.id,
                        document_id=source["document_id"],
                        field_path=source["field_path"],
                        stated_value=source["stated_value"],
                        evidence=source,
                        created_at=now,
                    )
                )
    session.flush()


def conflicts_for(session: Session, *, job_id: str, client_id: str) -> list[dict[str, Any]]:
    records = session.scalars(
        select(ConflictRecord)
        .where(ConflictRecord.job_id == job_id, ConflictRecord.client_id == client_id)
        .order_by(ConflictRecord.canonical_key, ConflictRecord.id)
    ).all()
    ids = [c.id for c in records]
    sources: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    if ids:
        for source in session.scalars(
            select(ConflictSource)
            .where(ConflictSource.conflict_id.in_(ids))
            .order_by(ConflictSource.document_id, ConflictSource.field_path)
        ):
            sources[source.conflict_id].append(source.evidence)
    return [
        {
            "conflict_id": c.id,
            "canonical_key": c.canonical_key,
            "field_label": c.field_label,
            "status": c.status,
            "sources": sources[c.id],
        }
        for c in records
    ]
