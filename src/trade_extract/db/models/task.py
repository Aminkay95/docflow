from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class JobTask(Base):
    __tablename__ = "job_tasks"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('prepare_file', 'segment_file', 'extract_segment', "
            "'finalize_submission', 'deliver_webhook', 'purge_documents', 'recover_stale')",
            name="ck_job_tasks_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'dead')",
            name="ck_job_tasks_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_job_tasks_attempts"),
        CheckConstraint("max_attempts > 0", name="ck_job_tasks_max_attempts"),
        CheckConstraint("requeues >= 0", name="ck_job_tasks_requeues"),
        CheckConstraint(
            "status <> 'running' OR locked_by IS NOT NULL",
            name="ck_job_tasks_running_lock",
        ),
        Index(
            "ix_tasks_claim",
            "status",
            "run_after",
            text("priority DESC"),
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("ix_tasks_running", "locked_at", postgresql_where=text("status = 'running'")),
        Index("ix_tasks_job", "job_id", "created_at"),
        Index("ix_tasks_dead", "status", postgresql_where=text("status = 'dead'")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    submission_id: Mapped[str | None] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    segment_id: Mapped[str | None] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("100"))
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    requeues: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
