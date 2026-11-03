"""The only module that knows DeepSeek's HTTP wire format."""

from __future__ import annotations

import base64
import io
import json
import random
import time
from collections.abc import Callable
from dataclasses import replace

import httpx

from trade_extract.config import Settings
from trade_extract.domain.inference import (
    InferenceRequest,
    ProviderAttempt,
    ProviderError,
    ProviderResult,
)
from trade_extract.providers.base import AttemptRecorder
from trade_extract.providers.prompts import prompt_for, prompt_hash


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        recorder: AttemptRecorder,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.settings = settings
        self.client = client or httpx.Client()
        self.recorder = recorder
        self.sleep = sleep
        self.jitter = jitter

    def vision_extract(self, request: InferenceRequest) -> ProviderResult:
        return self._call(request, self.settings.deepseek_vision_model, vision=True)

    def text_reason(self, request: InferenceRequest) -> ProviderResult:
        return self._call(request, self.settings.deepseek_text_model, vision=False)

    def _call(self, request: InferenceRequest, model: str, *, vision: bool) -> ProviderResult:
        if request.pages and (not vision or model != "deepseek-flash"):
            raise ProviderError("image_capability_error")
        if len(request.pages) > self.settings.provider_max_images:
            raise ProviderError("image_limit_error")
        total = 0
        for page in request.pages:
            total += len(page.image_bytes)
            signatures = {
                "image/png": b"\x89PNG\r\n\x1a\n",
                "image/jpeg": b"\xff\xd8\xff",
                "image/webp": b"RIFF",
            }
            signature = signatures.get(page.content_type)
            if signature is None or not page.image_bytes.startswith(signature):
                raise ProviderError("image_format_error")
            if page.width_px <= 0 or page.height_px <= 0:
                raise ProviderError("image_limit_error")
        if total > self.settings.provider_max_image_bytes:
            raise ProviderError("image_limit_error")
        if request.pages:
            from PIL import Image

            for page in request.pages:
                try:
                    with Image.open(io.BytesIO(page.image_bytes)) as decoded:
                        if decoded.size != (page.width_px, page.height_px):
                            raise ProviderError("image_limit_error")
                        if decoded.width * decoded.height > 100_000_000:
                            raise ProviderError("image_limit_error")
                        if Image.MIME.get(decoded.format or "") != page.content_type:
                            raise ProviderError("image_format_error")
                        decoded.verify()
                except ProviderError:
                    raise
                except (OSError, ValueError, Image.DecompressionBombError):
                    raise ProviderError("image_format_error") from None
        content: list[dict[str, object]] = [{"type": "text", "text": request.evidence}]
        if request.repair_errors:
            content.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "repair_errors": request.repair_errors,
                            "previous_json": request.previous_json,
                        }
                    ),
                }
            )
        for page in request.pages:
            content.append({"type": "text", "text": f"Source page_index: {page.page_index}"})
            encoded = base64.b64encode(page.image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{page.content_type};base64,{encoded}",
                    },
                }
            )
        body = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt_for(request)},
                {"role": "user", "content": content},
            ],
        }
        digest = prompt_hash(request)
        for attempt in range(1, self.settings.provider_max_attempts + 1):
            started = time.monotonic()
            result: ProviderResult | None = None
            error: ProviderError | None = None
            try:
                response = self.client.post(
                    self.settings.deepseek_base_url.rstrip("/") + "/chat/completions",
                    headers={
                        "Authorization": "Bearer "
                        + self.settings.deepseek_api_key.get_secret_value()
                    },
                    json=body,
                    timeout=httpx.Timeout(
                        self.settings.provider_timeout_seconds,
                        connect=self.settings.provider_connect_timeout_seconds,
                    ),
                )
                if response.status_code >= 400:
                    error = ProviderError(
                        f"http_{response.status_code}",
                        retryable=(response.status_code == 429 or response.status_code >= 500),
                    )
                else:
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("invalid envelope")
                    usage = data.get("usage", {})
                    raw_json = data["choices"][0]["message"]["content"]
                    if not isinstance(raw_json, str) or not isinstance(usage, dict):
                        raise ValueError("invalid envelope")
                    tokens = [
                        int(usage.get(key, 0))
                        for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens")
                    ]
                    if any(value < 0 for value in tokens):
                        raise ValueError("invalid usage")
                    result = ProviderResult(
                        raw_json=raw_json,
                        model=model,
                        input_tokens=tokens[0],
                        output_tokens=tokens[1],
                        cached_tokens=tokens[2],
                    )
            except httpx.TimeoutException:
                error = ProviderError("provider_timeout", retryable=True)
            except (httpx.NetworkError, httpx.RemoteProtocolError):
                error = ProviderError("provider_connection_error", retryable=True)
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                error = ProviderError("provider_response_error")
            latency = int((time.monotonic() - started) * 1000)
            self.recorder(
                ProviderAttempt(
                    model=model,
                    prompt_hash=digest,
                    attempt=attempt,
                    status="succeeded" if result else "failed",
                    latency_ms=latency,
                    error_code=error.code if error else None,
                    input_tokens=result.input_tokens if result else 0,
                    output_tokens=result.output_tokens if result else 0,
                    cached_tokens=result.cached_tokens if result else 0,
                    segment_id=request.segment_id,
                    reader="vision" if vision else "text_layer",
                )
            )
            if result:
                return replace(result, latency_ms=latency)
            assert error is not None
            if not error.retryable or attempt == self.settings.provider_max_attempts:
                raise error from None
            self.sleep(
                min(self.settings.provider_backoff_max_seconds, 2 ** (attempt - 1) + self.jitter())
            )
        raise AssertionError("unreachable")
