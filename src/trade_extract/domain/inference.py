"""Provider-neutral inference contracts. No wire-format objects belong here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PageImage:
    page_index: int
    image_bytes: bytes
    width_px: int
    height_px: int
    content_type: str = "image/jpeg"


@dataclass(frozen=True)
class InferenceRequest:
    document_type: str
    source_hash: str
    schema_json: dict[str, Any]
    evidence: str
    pages: tuple[PageImage, ...] = ()
    repair_errors: tuple[str, ...] = ()
    previous_json: str | None = None
    segment_id: str | None = None


VisionRequest = InferenceRequest
TextRequest = InferenceRequest


@dataclass(frozen=True)
class ProviderResult:
    raw_json: str | dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0


@dataclass(frozen=True)
class ProviderAttempt:
    model: str
    prompt_hash: str
    attempt: int
    status: str
    latency_ms: int
    error_code: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    segment_id: str | None = None
    reader: str = "vision"


class ProviderError(Exception):
    """A content-free error, safe to expose to review integrations."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class ExtractionOutcome:
    status: str
    document: Any = None
    candidates: tuple[Any, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)
    routes: tuple[Any, ...] = ()
    inventory: Any = None
    diagnostics: tuple[str, ...] = ()
