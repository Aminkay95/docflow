from collections.abc import Callable
from typing import Protocol

from trade_extract.domain.inference import InferenceRequest, ProviderAttempt, ProviderResult

AttemptRecorder = Callable[[ProviderAttempt], None]


class ModelProvider(Protocol):
    name: str

    def vision_extract(self, request: InferenceRequest) -> ProviderResult: ...

    def text_reason(self, request: InferenceRequest) -> ProviderResult: ...
