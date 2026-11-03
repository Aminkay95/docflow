from dataclasses import replace
from functools import lru_cache
from typing import ClassVar, Protocol

from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import ReaderKind
from trade_extract.domain.readers import PageInput
from trade_extract.providers.base import ModelProvider
from trade_extract.schemas.base import DocumentEnvelope, FieldSet
from trade_extract.schemas.registry import get_schema


@lru_cache(maxsize=7)
def partial_model(document_type: str) -> type[DocumentEnvelope]:
    """Validate supplied scalar evidence; document completeness is checked after merge."""
    model = get_schema(document_type).model
    fields = FieldSet(
        tuple(replace(s, required=False) if not s.indexed else s for s in model.field_set.specs)
    )
    return type(
        "Partial" + model.__name__,
        (model,),
        {
            "__annotations__": {"field_set": ClassVar[FieldSet]},
            "field_set": fields,
        },
    )


class Reader(Protocol):
    name: ReaderKind

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None: ...
