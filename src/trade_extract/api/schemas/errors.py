from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Error(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    doc_url: str


class ErrorEnvelope(BaseModel):
    error: Error
