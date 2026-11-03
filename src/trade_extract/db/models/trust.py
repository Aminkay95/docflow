"""Conservative identities and retry-safe observations."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from trade_extract.db.base import Base


class TrustRecord(Base):
    __tablename__ = "trust_records"
    __table_args__ = (
        CheckConstraint("state IN ('unseen','observed','trusted')", name="ck_trust_state"),
        CheckConstraint("review_depth IN ('full','sampled')", name="ck_trust_depth"),
        CheckConstraint("correction_count >= 0", name="ck_trust_corrections"),
        UniqueConstraint(
            "client_id",
            "supplier_key",
            "document_type",
            "template_fingerprint",
            name="uq_trust_identity",
        ),
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"))
    supplier_key: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(Text)
    template_fingerprint: Mapped[str] = mapped_column(Text)
    review_depth: Mapped[str] = mapped_column(Text, default="full")
    state: Mapped[str] = mapped_column(Text, default="unseen")
    correction_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrustObservation(Base):
    __tablename__ = "trust_observations"
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    trust_record_id: Mapped[str] = mapped_column(ForeignKey("trust_records.id"), index=True)
    outcome: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
