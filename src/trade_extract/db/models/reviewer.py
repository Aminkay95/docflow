from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Reviewer(Base):
    __tablename__ = "reviewers"
    __table_args__ = (
        CheckConstraint("role IN ('reviewer', 'admin', 'auditor')", name="ck_reviewers_role"),
        Index("ux_reviewers_username_lower", text("lower(username)"), unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'reviewer'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
