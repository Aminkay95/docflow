from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_submissions_status",
        ),
        CheckConstraint("document_count >= 0", name="ck_submissions_document_count"),
        CheckConstraint("duplicate_count >= 0", name="ck_submissions_duplicate_count"),
        Index(
            "ux_submissions_client_idempotency",
            "client_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_submissions_job", "job_id", "created_at"),
        Index("ix_submissions_client_created", "client_id", text("created_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    result_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
