from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_serializer

from trade_extract.api.schemas.common import iso_z


class ExtractAccepted(BaseModel):
    result_id: str
    job_id: str
    job_ref: str | None = None
    status: Literal["queued", "processing", "completed", "failed"]
    submitted_documents: int
    duplicate_documents: int
    status_url: str
    job_url: str
    created_at: datetime

    @field_serializer("created_at")
    def _serialise_created_at(self, value: datetime) -> str:
        return iso_z(value)
