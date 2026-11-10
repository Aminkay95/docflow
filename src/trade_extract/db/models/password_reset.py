from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class PasswordReset(Base):
    """Single-use password reset token, stored only as a SHA-256 digest."""

    __tablename__ = "review_password_resets"
    __table_args__ = (Index("ix_review_password_resets_reviewer_id", "reviewer_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    reviewer_id: Mapped[str] = mapped_column(
        Text, ForeignKey("reviewers.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
