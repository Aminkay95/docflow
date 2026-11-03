from typing import Protocol

from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import ReaderKind
from trade_extract.domain.readers import PageInput
from trade_extract.providers.base import ModelProvider


class Reader(Protocol):
    name: ReaderKind

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None: ...
