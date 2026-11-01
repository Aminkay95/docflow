from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
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


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_files_size"),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_files_page_count",
        ),
        CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'failed', 'purged')",
            name="ck_files_status",
        ),
        Index("ux_files_job_sha256", "job_id", "sha256", unique=True),
        Index("ix_files_submission", "submission_id"),
        Index("ix_files_job", "job_id"),
        Index("ix_files_purgeable", "job_id", postgresql_where=text("purged_at IS NULL")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'received'"))
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
