"""Groq adapter using the shared OpenAI-compatible transport."""

import json
from dataclasses import replace
from typing import Any

from trade_extract.config import Settings
from trade_extract.domain.inference import InferenceRequest, ProviderError, ProviderResult
from trade_extract.providers.deepseek import DeepSeekProvider


class GroqProvider(DeepSeekProvider):
    name = "groq"

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__(settings, **kwargs)
        self._source_calls: dict[tuple[str, str | None], int] = {}

    def _call(self, request: InferenceRequest, model: str, *, vision: bool) -> ProviderResult:
        key = (request.source_hash, request.segment_id)
        count = self._source_calls.get(key, 0)
        if count >= self.settings.provider_max_calls_per_page * request.page_count:
            raise ProviderError("provider_call_budget_exhausted")
        self._source_calls[key] = count + 1
        return super()._call(request, model, vision=vision)

    def text_reason(self, request: InferenceRequest) -> ProviderResult:
        paths = request.schema_json.get("allowed_scalar_paths", [])
        if (
            request.schema_json.get("contract") != "evidence_values_v1"
            or len(paths) <= 6
            or request.schema_json.get("phase") in {"header_party", "totals_tax"}
        ):
            return self._call(request, self.settings.groq_text_model, vision=False)
        values = []
        tables = {}
        results = []
        for start in range(0, len(paths), 6):
            current = replace(
                request,
                schema_json={
                    **request.schema_json,
                    "allowed_scalar_paths": paths[start : start + 6],
                },
            )
            result = self._call(current, self.settings.groq_text_model, vision=False)
            raw = (
                json.loads(result.raw_json) if isinstance(result.raw_json, str) else result.raw_json
            )
            if not isinstance(raw, dict):
                raise ProviderError("provider_response_error")
            batch_values = raw.get("values")
            if isinstance(batch_values, dict):
                if any(not isinstance(value, dict) for value in batch_values.values()):
                    raise ProviderError("provider_response_error")
                batch_values = [dict(value, path=path) for path, value in batch_values.items()]
            if not isinstance(batch_values, list):
                raise ProviderError("provider_response_error")
            batch_tables = raw.get("tables", {})
            if not isinstance(batch_tables, dict):
                raise ProviderError("provider_response_error")
            for name, metadata in batch_tables.items():
                if name in tables and tables[name] != metadata:
                    raise ProviderError("provider_response_error")
                tables[name] = metadata
            values.extend(batch_values)
            results.append(result)
        return replace(
            results[-1],
            raw_json={"values": values, "tables": tables},
            input_tokens=sum(item.input_tokens for item in results),
            output_tokens=sum(item.output_tokens for item in results),
            cached_tokens=sum(item.cached_tokens for item in results),
            latency_ms=sum(item.latency_ms for item in results),
        )

    def vision_extract(self, request: InferenceRequest) -> ProviderResult:
        # The default Qwen vision model accepts up to three images per call.
        if len(request.pages) > 3:
            raise ProviderError("image_limit_error")
        return self._call(request, self.settings.groq_vision_model, vision=True)

    def _transport_options(self) -> tuple[str, dict[str, str], float]:
        return (
            self.settings.groq_base_url,
            {"Authorization": "Bearer " + self.settings.groq_api_key.get_secret_value()},
            self.settings.provider_timeout_seconds,
        )
