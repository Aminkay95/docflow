"""Accumulated job reads, separate from submission-specific results."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_serializer

from trade_extract.api.schemas.common import iso_z
from trade_extract.api.schemas.results import Conflict, ReviewCounts


class DocumentSummary(BaseModel):
    document_id: str
    document_type: str
    status: str
    decision: str | None
    created_at: datetime
    submitted_at: datetime

    @field_serializer("created_at", "submitted_at")
    def serialize_time(self, value: datetime) -> str:
        return iso_z(value)


class JobConflict(Conflict):
    source_count: int


class JobPolicy(BaseModel):
    version: int


class ConflictSummary(BaseModel):
    conflict_id: str
    canonical_key: str
    field_label: str
    status: Literal["open", "resolved", "waived"]
    source_count: int


class Job(BaseModel):
    job_id: str
    job_ref: str | None
    status: Literal["queued", "processing", "review_required", "completed", "failed"]
    created_at: datetime
    last_submission_at: datetime
    terminal_at: datetime | None
    document_count: int
    documents: list[DocumentSummary]
    conflicts: list[ConflictSummary]
    review: ReviewCounts
    policy: JobPolicy

    @field_serializer("created_at", "last_submission_at", "terminal_at")
    def serialize_time(self, value: datetime | None) -> str | None:
        return iso_z(value) if value else None


class PaginatedDocuments(BaseModel):
    data: list[DocumentSummary]
    has_more: bool
    next_cursor: str | None


class PaginatedConflicts(BaseModel):
    data: list[JobConflict]
    has_more: bool
    next_cursor: str | None
