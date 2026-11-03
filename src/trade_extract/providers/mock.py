"""Deterministic, explicitly scripted mock; never invents document values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from trade_extract.domain.inference import (
    InferenceRequest,
    ProviderAttempt,
    ProviderError,
    ProviderResult,
)
from trade_extract.providers.base import AttemptRecorder
from trade_extract.providers.prompts import prompt_hash


class MockProvider:
    name = "mock"

    def __init__(
        self,
        fixtures: Mapping[tuple[str, str], Sequence[ProviderResult | ProviderError]] | None = None,
        *,
        recorder: AttemptRecorder = lambda attempt: None,
    ) -> None:
        self.fixtures = {key: tuple(value) for key, value in (fixtures or {}).items()}
        self.positions: dict[tuple[str, str], int] = {}
        self.recorder = recorder
        self.requests: list[InferenceRequest] = []

    def _call(self, request: InferenceRequest, reader: str) -> ProviderResult:
        self.requests.append(request)
        key = (request.document_type, request.source_hash)
        script = self.fixtures.get(key, ())
        index = self.positions.get(key, 0)
        self.positions[key] = index + 1
        result = (
            script[min(index, len(script) - 1)] if script else ProviderError("mock_fixture_missing")
        )
        self.recorder(
            ProviderAttempt(
                model=result.model if isinstance(result, ProviderResult) else "mock",
                prompt_hash=prompt_hash(request),
                attempt=index + 1,
                status="failed" if isinstance(result, ProviderError) else "succeeded",
                latency_ms=0,
                error_code=result.code if isinstance(result, ProviderError) else None,
                input_tokens=result.input_tokens if isinstance(result, ProviderResult) else 0,
                output_tokens=result.output_tokens if isinstance(result, ProviderResult) else 0,
                cached_tokens=result.cached_tokens if isinstance(result, ProviderResult) else 0,
                segment_id=request.segment_id,
                reader=reader,
            )
        )
        if isinstance(result, ProviderError):
            raise result
        return result

    def vision_extract(self, request: InferenceRequest) -> ProviderResult:
        return self._call(request, "vision")

    def text_reason(self, request: InferenceRequest) -> ProviderResult:
        if request.pages:
            raise ProviderError("image_capability_error")
        return self._call(request, "text_layer")
