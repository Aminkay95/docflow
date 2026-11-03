"""Retained check, decision and conflict evidence."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class Check(Base):
    __tablename__ = "checks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "code", "field_path", "check_version", name="uq_checks_identity"
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(Text)
    field_path: Mapped[str] = mapped_column(Text)
    check_version: Mapped[str] = mapped_column(Text)
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DecisionRecord(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id", ondelete="RESTRICT"))
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConflictRecord(Base):
    __tablename__ = "conflicts"
    __table_args__ = (
        CheckConstraint("status IN ('open','resolved','waived')", name="ck_conflicts_status"),
        Index(
            "ux_conflicts_open_key",
            "job_id",
            "canonical_key",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    canonical_key: Mapped[str] = mapped_column(Text)
    field_label: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ConflictSource(Base):
    __tablename__ = "conflict_sources"
    __table_args__ = (
        UniqueConstraint(
            "conflict_id", "document_id", "field_path", name="uq_conflict_sources_identity"
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    conflict_id: Mapped[str] = mapped_column(
        ForeignKey("conflicts.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    field_path: Mapped[str] = mapped_column(Text)
    stated_value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint(
            "(track = 'extraction' AND document_id IS NOT NULL AND conflict_id IS NULL) OR "
            "(track = 'conflict' AND conflict_id IS NOT NULL AND document_id IS NULL)",
            name="ck_review_items_track",
        ),
        Index(
            "ux_review_open_document",
            "document_id",
            unique=True,
            postgresql_where=text("track = 'extraction' AND status IN ('open','in_progress')"),
        ),
        Index(
            "ux_review_open_conflict",
            "conflict_id",
            unique=True,
            postgresql_where=text("track = 'conflict' AND status IN ('open','in_progress')"),
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    conflict_id: Mapped[str | None] = mapped_column(ForeignKey("conflicts.id", ondelete="CASCADE"))
    track: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    reason_code: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
