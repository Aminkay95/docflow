from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trade_extract.db.base import Base

if TYPE_CHECKING:
    from trade_extract.db.models.client import Client


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_client", "client_id", postgresql_where=text("revoked_at IS NULL")),
        Index(
            "ux_api_keys_prefix_active",
            "key_prefix",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("ARRAY['extract:write', 'results:read']"),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    client: Mapped[Client] = relationship(back_populates="api_keys")
