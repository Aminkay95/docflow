from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'review_required', 'completed', 'failed')",
            name="ck_jobs_status",
        ),
        CheckConstraint("document_count >= 0", name="ck_jobs_document_count"),
        CheckConstraint("open_review_count >= 0", name="ck_jobs_open_review_count"),
        CheckConstraint("open_conflict_count >= 0", name="ck_jobs_open_conflict_count"),
        CheckConstraint(
            "terminal_reason IS NULL OR terminal_reason IN ('completed', 'failed', 'cancelled')",
            name="ck_jobs_terminal_reason",
        ),
        CheckConstraint(
            "(terminal_at IS NULL) = (terminal_reason IS NULL)",
            name="ck_jobs_terminal_pair",
        ),
        Index(
            "ux_jobs_client_ref",
            "client_id",
            "job_ref",
            unique=True,
            postgresql_where=text("job_ref IS NOT NULL"),
        ),
        Index("ix_jobs_client_created", "client_id", text("created_at DESC")),
        Index("ix_jobs_status", "status", "updated_at"),
        Index("ix_jobs_terminal", "terminal_at", postgresql_where=text("terminal_at IS NOT NULL")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    job_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("policies.id", ondelete="RESTRICT"), nullable=False
    )
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    open_review_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    open_conflict_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_submission_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
