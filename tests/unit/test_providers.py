import json
from dataclasses import asdict, replace

import httpx
import pytest
from pydantic import ValidationError

from tests.support import make_image
from trade_extract.config import Settings
from trade_extract.domain.inference import InferenceRequest, PageImage, ProviderError
from trade_extract.providers import make_provider
from trade_extract.providers.deepseek import DeepSeekProvider, LocalModelProvider
from trade_extract.providers.prompts import prompt_hash


def request():
    return InferenceRequest("commercial_invoice", "a" * 64, {}, "private document")


@pytest.mark.parametrize("status,count", [(400, 1), (429, 3), (503, 3)])
def test_http_retry_and_content_free_attempts(status, count, caplog):
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
    errors = [vars(record) for record in caplog.records if record.msg == "provider_http_error"]
    assert len(errors) == count
    assert all("sensitive response" not in str(error) for error in errors)
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
        Settings(provider="deepseek", deepseek_api_key="secret", provider_max_output_tokens=8192),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=attempts.append,
        sleep=lambda seconds: None,
    )
    assert provider.text_reason(request()).input_tokens == 12
    assert len(attempts) == 2
    assert b'"response_format":{"type":"json_object"}' in calls[-1].content
    assert json.loads(calls[-1].content)["max_tokens"] == 8192


def test_output_token_budget_is_forwarded_and_capped():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = DeepSeekProvider(
        Settings(
            provider="deepseek",
            deepseek_api_key="secret",
            provider_max_output_tokens=1024,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=lambda attempt: None,
    )
    provider.text_reason(request())

    assert calls[0]["max_tokens"] == 1024
    assert calls[0]["thinking"] == {"type": "disabled"}
    with pytest.raises(ValidationError):
        Settings(provider_max_output_tokens=65537)


def test_images_rejected_before_http_and_prompt_stability(monkeypatch):
    monkeypatch.setattr(
        "trade_extract.config.get_settings", lambda: Settings(stage_prompts_enabled=False)
    )
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
    assert make_provider(Settings(provider="mock")).name == "mock"


def test_local_model_uses_llama_server_for_text_and_vision():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    settings = Settings(
        _env_file=None,
        provider="local",
        deepseek_api_key="",
        provider_input_price_usd_per_million=100,
        provider_output_price_usd_per_million=100,
    )
    attempts = []
    provider = make_provider(settings, recorder=attempts.append)
    assert isinstance(provider, LocalModelProvider)
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert provider.text_reason(request()).model == settings.local_model_name
    provider.vision_extract(replace(request(), pages=(PageImage(0, make_image(), 16, 16),)))

    assert [attempt.reader for attempt in attempts] == ["text_layer", "vision"]
    for call in calls:
        assert str(call.url) == "http://127.0.0.1:8080/v1/chat/completions"
        assert "Authorization" not in call.headers
        body = json.loads(call.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "thinking" not in body
    assert json.loads(calls[1].content)["messages"][1]["content"][-1]["type"] == "image_url"


def test_local_totals_request_keeps_evidence_ids_and_omits_coordinates():
    from trade_extract.services.readers.base import invoice_phase_schema

    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = LocalModelProvider(
        Settings(provider="local"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        recorder=lambda attempt: None,
    )
    schema = invoice_phase_schema("commercial_invoice", "totals_tax")
    evidence = (
        "p0l0|p=0|b=0,0,1,1|Invoice header\n"
        "p0l1|p=0|b=0,0,1,1|GBP\n"
        "p0l2|p=0|b=0,0,1,1|Total amount due: 250.50\n"
        "Independent rows: []"
    )
    provider.text_reason(replace(request(), evidence=evidence, schema_json=schema))
    assert calls[0]["messages"][1]["content"][0]["text"] == (
        "p0l1|GBP\np0l2|Total amount due: 250.50"
    )
    assert calls[0]["response_format"]["schema"] == schema["output"]


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


def test_input_budget_and_truncated_output_fail_with_named_errors_before_promotion():
    calls = []
    provider = DeepSeekProvider(
        Settings(
            provider="deepseek",
            deepseek_api_key="secret",
            provider_max_estimated_input_tokens_per_page=1,
        ),
        recorder=lambda attempt: None,
        client=httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req))),
    )
    with pytest.raises(ProviderError, match="provider_input_budget_exhausted"):
        provider.text_reason(request())
    assert not calls

    attempts = []
    provider = DeepSeekProvider(
        Settings(provider="deepseek", deepseek_api_key="secret"),
        recorder=attempts.append,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": '{"partial":'}, "finish_reason": "length"}
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
                    },
                )
            )
        ),
    )
    with pytest.raises(ProviderError, match="provider_output_truncated"):
        provider.text_reason(request())
    assert attempts[0].status == "failed"
    assert attempts[0].input_tokens == 10
    assert attempts[0].output_tokens == 4096


@pytest.mark.parametrize("backend", ["groq", "deepseek", "local"])
def test_environment_provider_switch(monkeypatch, backend):
    monkeypatch.setenv("PROVIDER", backend)
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-secret")
    settings = Settings(_env_file=None)
    assert make_provider(settings, recorder=lambda attempt: None).name == backend


def test_groq_requires_own_key_and_https():
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        Settings(provider="groq", groq_api_key="", deepseek_api_key="other-secret")
    with pytest.raises(ValidationError, match="GROQ_BASE_URL"):
        Settings(groq_base_url="http://insecure.example")
    settings = Settings(provider="groq", groq_api_key="private-groq-secret")
    assert "private-groq-secret" not in repr(settings)
    with pytest.raises(ValueError, match="live_provider_requires_recorder"):
        make_provider(settings)


def test_groq_text_and_vision_use_own_models_and_compatible_payload():
    from trade_extract.providers.groq import GroqProvider

    calls, attempts = [], []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 7},
            },
        )

    settings = Settings(
        provider="groq",
        groq_api_key="groq-secret",
        groq_text_model="text-model",
        groq_vision_model="vision-model",
    )
    provider = GroqProvider(
        settings,
        recorder=attempts.append,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.text_reason(request()).input_tokens == 42
    image_bytes = make_image("PNG")
    import io

    from PIL import Image

    width, height = Image.open(io.BytesIO(image_bytes)).size
    image = PageImage(0, image_bytes, width, height, content_type="image/png")
    assert provider.vision_extract(replace(request(), pages=(image,))).model == "vision-model"
    for req, model in zip(calls, ("text-model", "vision-model"), strict=True):
        body = json.loads(req.content)
        assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer groq-secret"
        assert body["model"] == model
        assert body["response_format"] == {"type": "json_object"}
        assert "thinking" not in body and "chat_template_kwargs" not in body
    assert any(
        part["type"] == "image_url"
        for part in json.loads(calls[1].content)["messages"][1]["content"]
    )
    assert len(attempts) == 2
    with pytest.raises(ProviderError, match="image_limit_error"):
        provider.vision_extract(replace(request(), pages=(image,) * 4))
    assert len(calls) == 2


def test_groq_rate_limit_is_retried_and_recorded():
    from trade_extract.providers.groq import GroqProvider

    calls, attempts = [], []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = GroqProvider(
        Settings(provider="groq", groq_api_key="test"),
        recorder=attempts.append,
        sleep=lambda seconds: None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider.text_reason(request())
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]


def test_groq_image_tokens_and_backend_prices_enforce_budgets():
    from trade_extract.providers.groq import GroqProvider

    settings = Settings(
        provider="groq", groq_api_key="test", provider_max_estimated_input_tokens_per_page=2000
    )
    provider = GroqProvider(settings, recorder=lambda attempt: None)
    image = PageImage(0, make_image(), 16, 16)
    with pytest.raises(ProviderError, match="provider_input_budget_exhausted"):
        provider.vision_extract(replace(request(), pages=(image,)))
    settings = Settings(
        provider="groq",
        groq_api_key="test",
        provider_cost_target_usd_per_page=0.01,
        provider_cost_safety_ceiling_usd_per_page=0.02,
        groq_max_output_tokens=8192,
    )
    provider = GroqProvider(settings, recorder=lambda attempt: None)
    with pytest.raises(ProviderError, match="provider_cost_safety_ceiling_exhausted"):
        provider.text_reason(request())


def test_groq_compact_batches_preserve_global_paths_and_cap_output():
    from trade_extract.providers.groq import GroqProvider
    from trade_extract.services.readers.base import invoice_phase_schema

    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        prompt = body["messages"][0]["content"]
        paths = prompt.split(" Field paths: ", 1)[1].split(", ")
        assert len(paths) <= 6
        assert body["max_tokens"] == 900
        assert len(prompt) < 2500
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "values": [
                                        {"path": path, "value": "printed", "evidence_id": "p0l0"}
                                        for path in paths
                                    ],
                                    "tables": {},
                                }
                            )
                        }
                    }
                ]
            },
        )

    schema = invoice_phase_schema("bill_of_lading", "containers", rows=(5,))
    provider = GroqProvider(
        Settings(provider="groq", groq_api_key="test"),
        recorder=lambda attempt: None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    current = replace(
        request(),
        document_type="bill_of_lading",
        schema_json=schema,
        evidence="p0l0|p=0|b=0,0,1,1|printed",
    )
    raw = provider.text_reason(current).raw_json
    assert {item["path"] for item in raw["values"]} == set(schema["allowed_scalar_paths"])
    assert all("[5]" in item["path"] for item in raw["values"])
    assert len(calls) == 2


def test_groq_batch_calls_respect_document_budget():
    from trade_extract.providers.groq import GroqProvider
    from trade_extract.services.readers.base import invoice_phase_schema

    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"values": [], "tables": {}}'}}]}
        )

    provider = GroqProvider(
        Settings(provider="groq", groq_api_key="test", provider_max_calls_per_page=1),
        recorder=lambda attempt: None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    current = replace(
        request(), schema_json=invoice_phase_schema("bill_of_lading", "containers", rows=(0,))
    )
    with pytest.raises(ProviderError, match="provider_call_budget_exhausted"):
        provider.text_reason(current)
    assert len(calls) == 1


@pytest.mark.parametrize("phase", ["header_party", "totals_tax"])
def test_groq_scalar_phase_avoids_empty_field_batch_storm(phase):
    from trade_extract.providers.groq import GroqProvider
    from trade_extract.services.readers.base import invoice_phase_schema

    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        assert body["max_tokens"] == 900
        assert body["reasoning_effort"] == "none"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"values": [], "tables": {}}'}}]}
        )

    provider = GroqProvider(
        Settings(provider="groq", groq_api_key="test"),
        recorder=lambda attempt: None,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    schema = invoice_phase_schema("commercial_invoice", phase)
    provider.text_reason(replace(request(), schema_json=schema))
    assert len(calls) == 1


def test_groq_batched_empty_value_maps_are_valid():
    from trade_extract.providers.groq import GroqProvider
    from trade_extract.services.readers.base import invoice_phase_schema

    provider = GroqProvider(
        Settings(provider="groq", groq_api_key="test"),
        recorder=lambda attempt: None,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200, json={"choices": [{"message": {"content": '{"values":{},"tables":{}}'}}]}
                )
            )
        ),
    )
    current = replace(
        request(), schema_json=invoice_phase_schema("commercial_invoice", "line_items", rows=(0,))
    )
    assert provider.text_reason(current).raw_json == {"values": [], "tables": {}}
