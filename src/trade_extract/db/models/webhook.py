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


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("submission_id", "event_type", name="uq_webhooks_submission_event"),
        CheckConstraint(
            "event_type IN ('result.completed', 'result.review_required', 'result.failed')",
            name="ck_webhooks_event_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'delivering', 'delivered', 'failed', 'dead')",
            name="ck_webhooks_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_webhooks_attempts"),
        Index(
            "ix_webhooks_due",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
        ),
        Index("ix_webhooks_submission", "submission_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
