from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("job_id", "idempotency_key", name="uq_documents_job_idempotency"),
        UniqueConstraint("job_id", "document_hash", name="uq_documents_job_hash"),
        CheckConstraint(
            "document_type IN ('commercial_invoice', 'proforma_invoice', 'bill_of_lading', "
            "'delivery_order', 'port_pass', 'custom_entry', 'verification_memo', "
            "'packing_list', 'unknown')",
            name="ck_documents_document_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'extracted', 'review_required', 'resolved', "
            "'unreadable', 'failed')",
            name="ck_documents_status",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('pass', 'review', 'unreadable', 'failed')",
            name="ck_documents_decision",
        ),
        CheckConstraint("page_start >= 0", name="ck_documents_page_start"),
        CheckConstraint("page_end >= page_start", name="ck_documents_page_end"),
        Index("ix_documents_job", "job_id", "created_at"),
        Index("ix_documents_submission", "submission_id"),
        Index("ix_documents_client_created", "client_id", text("created_at DESC")),
        Index("ix_documents_status", "status"),
        Index("ix_documents_type", "document_type"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    document_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    document_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    provenance_version: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'1.0'")
    )
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
