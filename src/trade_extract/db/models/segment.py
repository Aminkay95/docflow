from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        CheckConstraint("page_start >= 0", name="ck_segments_page_start"),
        CheckConstraint("page_end >= page_start", name="ck_segments_page_end"),
        CheckConstraint(
            "document_type IN ('commercial_invoice', 'proforma_invoice', 'bill_of_lading', "
            "'delivery_order', 'port_pass', 'custom_entry', 'verification_memo', "
            "'packing_list', 'unknown')",
            name="ck_segments_document_type",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_segments_confidence"),
        CheckConstraint(
            "status IN ('pending', 'extracting', 'extracted', 'failed')",
            name="ck_segments_status",
        ),
        Index("ix_segments_file", "file_id", "page_start"),
        Index("ix_segments_job", "job_id"),
        Index("ix_segments_type", "document_type"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
