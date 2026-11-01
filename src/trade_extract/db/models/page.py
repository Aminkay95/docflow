from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Page(Base):
    """The page atom: rendered image, text-layer facts and conditioning state."""

    __tablename__ = "pages"
    __table_args__ = (
        CheckConstraint("page_index >= 0", name="ck_pages_page_index"),
        CheckConstraint("width_px > 0", name="ck_pages_width"),
        CheckConstraint("height_px > 0", name="ck_pages_height"),
        CheckConstraint("dpi > 0", name="ck_pages_dpi"),
        CheckConstraint("text_layer_chars >= 0", name="ck_pages_text_layer_chars"),
        CheckConstraint(
            "legibility_score BETWEEN 0 AND 1",
            name="ck_pages_legibility_score",
        ),
        CheckConstraint(
            "status IN ('pending', 'conditioned', 'unreadable', 'read')",
            name="ck_pages_status",
        ),
        UniqueConstraint("file_id", "page_index", name="ux_pages_file_page"),
        Index("ix_pages_file", "file_id", "page_index"),
        Index("ix_pages_status", "status"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    dpi: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("300"))
    rotation_deg: Mapped[float] = mapped_column(
        Numeric(6, 2), nullable=False, server_default=text("0")
    )
    text_layer_present: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    text_layer_chars: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    text_layer: Mapped[str | None] = mapped_column(Text, nullable=True)
    legibility_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    conditioning: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    conditioning_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    unreadable_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    conditioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
