"""Trust observations never reduce v1 review depth."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from trade_extract.db.models.document import Document
from trade_extract.db.models.trust import TrustObservation, TrustRecord
from trade_extract.domain.ids import new_id


def observe(session: Session, document: Document, now: datetime) -> TrustRecord:
    session.execute(select(Document.id).where(Document.id == document.id).with_for_update()).one()
    observation = session.get(TrustObservation, document.id)
    if observation is not None:
        return session.scalars(
            select(TrustRecord)
            .where(TrustRecord.id == observation.trust_record_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
    # Unknown suppliers are isolated per document rather than pooled into earned trust.
    supplier = document.supplier_key or f"unknown:{document.id}"
    fingerprint = document.template_fingerprint or f"unknown:{document.id}"
    session.execute(
        insert(TrustRecord)
        .values(
            id=new_id("tru_"),
            client_id=document.client_id,
            supplier_key=supplier,
            document_type=document.document_type,
            template_fingerprint=fingerprint,
            review_depth="full",
            state="unseen",
            correction_count=0,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_trust_identity")
    )
    record = session.scalar(
        select(TrustRecord)
        .where(
            TrustRecord.client_id == document.client_id,
            TrustRecord.supplier_key == supplier,
            TrustRecord.document_type == document.document_type,
            TrustRecord.template_fingerprint == fingerprint,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert record is not None
    session.add(
        TrustObservation(
            document_id=document.id,
            trust_record_id=record.id,
            outcome=document.status,
            created_at=now,
        )
    )
    return record


def demote(session: Session, document: Document, now: datetime) -> None:
    record = observe(session, document, now)
    record.review_depth = "full"
    record.state = "unseen"
    record.correction_count += 1
    record.updated_at = now
