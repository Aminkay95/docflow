from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_policies_version"),
        CheckConstraint("status IN ('draft', 'active', 'retired')", name="ck_policies_status"),
        CheckConstraint(
            "status <> 'active' OR activated_at IS NOT NULL",
            name="ck_policies_active_at",
        ),
        Index(
            "ux_policies_one_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_policies_status", "status", text("version DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("reviewers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
