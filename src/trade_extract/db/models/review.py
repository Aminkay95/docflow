"""Retained human labels; extraction snapshots remain immutable."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class ReviewResolution(Base):
    __tablename__ = "review_resolutions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    review_item_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("reviewers.id"))
    field_path: Mapped[str | None] = mapped_column(Text)
    prior_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    corrected_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    chosen_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
