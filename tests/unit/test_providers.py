import json
from dataclasses import asdict, replace

import httpx
import pytest
from pydantic import ValidationError

from tests.support import make_image
from trade_extract.config import Settings
from trade_extract.domain.inference import InferenceRequest, PageImage, ProviderError
from trade_extract.providers import make_provider
from trade_extract.providers.deepseek import DeepSeekProvider
from trade_extract.providers.prompts import prompt_hash


def request():
    return InferenceRequest("commercial_invoice", "a" * 64, {}, "private document")


@pytest.mark.parametrize("status,count", [(400, 1), (429, 3), (503, 3)])
def test_http_retry_and_content_free_attempts(status, count):
    attempts = []
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="sensitive response")

    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="private-key"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=attempts.append,
        sleep=lambda seconds: None,
        jitter=lambda: 0,
    )
    with pytest.raises(ProviderError, match=f"http_{status}"):
        provider.text_reason(request())
    assert len(calls) == len(attempts) == count
    assert all("private" not in str(asdict(attempt)) for attempt in attempts)
    assert "private-key" not in repr(provider.settings)


def test_connection_retry_then_success_and_json_mode():
    calls = []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ReadTimeout("sensitive", request=req)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 12}}
        )

    attempts = []
    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=attempts.append,
        sleep=lambda seconds: None,
    )
    assert provider.text_reason(request()).input_tokens == 12
    assert len(attempts) == 2
    assert b'"response_format":{"type":"json_object"}' in calls[-1].content


def test_images_rejected_before_http_and_prompt_stability():
    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"), recorder=lambda attempt: None
    )
    image = PageImage(0, make_image(), 16, 16)
    with pytest.raises(ProviderError, match="image_capability"):
        provider.text_reason(replace(request(), pages=(image,)))
    with pytest.raises(ProviderError, match="image_format"):
        provider.vision_extract(
            replace(request(), pages=(replace(image, content_type="image/tiff"),))
        )
    assert prompt_hash(request()) == prompt_hash(replace(request(), evidence="different"))
    assert (
        prompt_hash(request()) == "1ae5acd1d47c207fb6c8870822688b3f2907801ab6a054ee04b9db17a4d205a5"
    )
    with pytest.raises(ValidationError):
        Settings(deepseek_vision_model="deepseek-v4-pro")


@pytest.mark.parametrize(
    "body", ["not JSON", "[]", "{}", '{"choices":[{"message":{"content":null}}]}']
)
def test_malformed_http_envelopes_are_recorded_as_errors(body):
    attempts = []
    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))
        ),
        recorder=attempts.append,
    )
    with pytest.raises(ProviderError, match="provider_response_error"):
        provider.text_reason(request())
    assert len(attempts) == 1
    assert attempts[0].status == "failed"
    assert attempts[0].error_code == "provider_response_error"
    assert attempts[0].reader == "text_layer"


def test_excessive_images_fail_before_http():
    calls = []
    provider = DeepSeekProvider(
        Settings(
            provider="deepseek",
            deepseek_api_key="secret",
            provider_max_images=1,
            provider_max_image_bytes=1,
        ),
        client=httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req))),
        recorder=lambda attempt: None,
    )
    image = PageImage(0, make_image(), 16, 16)
    for pages in ((image, image), (image,)):
        with pytest.raises(ProviderError, match="image_limit"):
            provider.vision_extract(replace(request(), pages=pages))
    assert not calls


def test_vision_wire_payload_keeps_images_in_user_message():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=lambda attempt: None,
    )
    result = provider.vision_extract(
        replace(request(), pages=(PageImage(0, make_image(), 16, 16),))
    )
    assert result.model == "deepseek-flash"
    assert calls[0]["messages"][0]["role"] == "system"
    assert isinstance(calls[0]["messages"][0]["content"], str)
    assert calls[0]["messages"][1]["content"][-1]["type"] == "image_url"


def test_live_provider_requires_explicit_recorder():
    settings = Settings(provider="deepseek", deepseek_api_key="secret")
    with pytest.raises(TypeError):
        DeepSeekProvider(settings)
    with pytest.raises(ValueError, match="live_provider_requires_recorder"):
        make_provider(settings)
    assert make_provider(Settings()).name == "mock"


def test_remote_protocol_errors_retry_and_record():
    attempts = []

    def handler(req):
        raise httpx.RemoteProtocolError("private response", request=req)

    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"),
        recorder=attempts.append,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda seconds: None,
    )
    with pytest.raises(ProviderError, match="provider_connection_error"):
        provider.text_reason(request())
    assert len(attempts) == 3
    assert all(a.status == "failed" for a in attempts)


def test_infinite_usage_is_a_recorded_sanitized_error():
    attempts = []
    body = '{"choices":[{"message":{"content":"{}"}}],"usage":{"prompt_tokens":1e999}}'
    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"),
        recorder=attempts.append,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body))
        ),
    )
    with pytest.raises(ProviderError, match="provider_response_error"):
        provider.text_reason(request())
    assert len(attempts) == 1 and attempts[0].status == "failed"
