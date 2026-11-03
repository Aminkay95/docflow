from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from trade_extract.api.schemas.common import iso_z
from trade_extract.domain.fields import Field as DocumentField
from trade_extract.schemas.base import EnvelopeSource, TableMeta


class Progress(BaseModel):
    documents_total: int = 0
    documents_completed: int = 0
    documents_failed: int = 0
    pages_total: int = 0
    pages_read: int = 0


class Decision(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Literal["pass", "review", "unreadable", "failed"]
    route: Literal["none", "extraction", "conflict"] | None = None
    reasons: list[str] = Field(default_factory=list)
    policy_id: str | None = None
    policy_version: int | None = None
    policy_snapshot: dict[str, Any] | None = None
    policy_valid: bool | None = None
    check_version: str | None = None
    diagnostics: list[str] = Field(default_factory=list)
    routing: list[dict[str, Any]] = Field(default_factory=list)


class ReviewCounts(BaseModel):
    extraction_open: int = 0
    conflict_open: int = 0


class DocumentReview(BaseModel):
    extraction_open: bool = False
    conflict_open: bool = False
    url: str | None = None


class Conflict(BaseModel):
    conflict_id: str
    canonical_key: str
    field_label: str
    status: Literal["open", "resolved", "waived"]
    sources: list[dict[str, Any]] = Field(default_factory=list)


class Document(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_id: str
    status: Literal[
        "queued", "processing", "extracted", "review_required", "resolved", "unreadable", "failed"
    ]
    result_id: str | None = None
    job_id: str | None = None
    job_ref: str | None = None
    file_id: str | None = None
    segment_id: str | None = None
    document_type: (
        Literal[
            "commercial_invoice",
            "proforma_invoice",
            "bill_of_lading",
            "delivery_order",
            "port_pass",
            "custom_entry",
            "verification_memo",
            "packing_list",
            "unknown",
        ]
        | None
    ) = None
    document_type_confidence: float | None = None
    schema_version: str | None = None
    source: EnvelopeSource | None = None
    fields: dict[str, DocumentField] | None = None
    tables: dict[str, TableMeta] | None = None
    extension: dict[str, DocumentField] | None = None
    checks: list[dict[str, Any]] | None = None
    conflicts: list[Conflict] | None = None
    decision: Decision | None = None
    review: DocumentReview | None = None
    created_at: datetime | None = None
    extracted_at: datetime | None = None

    @field_serializer("created_at", "extracted_at")
    def _serialise_timestamp(self, value: datetime | None) -> str | None:
        return iso_z(value) if value is not None else None


class PolicySummary(BaseModel):
    version: int
    effective_at: datetime | None = None

    @field_serializer("effective_at")
    def _serialise_effective_at(self, value: datetime | None) -> str | None:
        return iso_z(value) if value is not None else None


class Result(BaseModel):
    result_id: str
    job_id: str
    job_ref: str | None = None
    status: Literal["queued", "processing", "completed", "failed"]
    progress: Progress
    created_at: datetime
    completed_at: datetime | None = None
    documents: list[Document]
    conflicts: list[Conflict]
    decision: Decision | None
    review: ReviewCounts
    policy: PolicySummary

    @field_serializer("created_at")
    def _serialise_created_at(self, value: datetime) -> str:
        return iso_z(value)

    @field_serializer("completed_at")
    def _serialise_completed_at(self, value: datetime | None) -> str | None:
        return iso_z(value) if value is not None else None
