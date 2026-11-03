"""Worker-side provider factory; importing the API never imports this package."""

from trade_extract.config import Settings
from trade_extract.providers.base import AttemptRecorder, ModelProvider


def make_provider(settings: Settings, *, recorder: AttemptRecorder | None = None) -> ModelProvider:
    if settings.provider == "mock":
        from trade_extract.providers.mock import MockProvider

        return MockProvider(recorder=recorder) if recorder is not None else MockProvider()
    from trade_extract.providers.deepseek import DeepSeekProvider

    if recorder is None:
        raise ValueError("live_provider_requires_recorder")
    return DeepSeekProvider(settings, recorder=recorder)
