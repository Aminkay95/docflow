from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Reviewer(Base):
    __tablename__ = "reviewers"
    __table_args__ = (
        CheckConstraint(
            "(role = 'client' AND client_id IS NOT NULL AND NOT is_active) OR "
            "(role IN ('reviewer', 'admin', 'auditor') AND client_id IS NULL "
            "AND NOT client_login_enabled)",
            name="ck_reviewers_role",
        ),
        Index("ux_reviewers_username_lower", text("lower(username)"), unique=True),
        Index("ux_reviewers_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT", name="fk_reviewers_client"), index=True
    )
    username: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'reviewer'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Staff binaries predating the customer workspace only read is_active.
    # Keep customer accounts inactive to those binaries during a rollback.
    client_login_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    @property
    def can_sign_in(self) -> bool:
        return self.client_login_enabled if self.role == "client" else self.is_active

    session_epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
