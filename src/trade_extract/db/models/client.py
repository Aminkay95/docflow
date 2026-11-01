from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_extract.db.base import Base

if TYPE_CHECKING:
    from trade_extract.db.models.api_key import ApiKey


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'terminated')",
            name="ck_clients_status",
        ),
        CheckConstraint(
            "retention_days_after_termination >= 0",
            name="ck_clients_retention_days",
        ),
        CheckConstraint(
            "webhook_enabled = false OR webhook_url IS NOT NULL",
            name="ck_clients_webhook_url",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    webhook_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    retention_days_after_termination: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("90")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="client")
