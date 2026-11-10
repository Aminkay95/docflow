from copy import deepcopy
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


def compact_schema(document_type: str, resolved_paths: frozenset[str]) -> dict[str, object]:
    """Return the provider contract with already verified scalar paths removed."""
    schema = deepcopy(partial_model(document_type).json_schema())
    fields = schema.get("properties", {}).get("fields", {})
    if isinstance(fields, dict):
        properties = fields.get("properties", {})
        if isinstance(properties, dict):
            for path in resolved_paths:
                properties.pop(path, None)
        required = fields.get("required", [])
        if isinstance(required, list):
            fields["required"] = [path for path in required if path not in resolved_paths]
    return schema


def compact_value_schema(document_type: str, resolved_paths: frozenset[str]) -> dict[str, object]:
    """Small provider contract; trusted provenance is reconstructed locally."""
    field_set = get_schema(document_type).model.field_set
    scalar_paths = [
        spec.path
        for spec in field_set.scalar_specs
        if spec.required and spec.path not in resolved_paths
    ]
    return {
        "contract": "evidence_values_v1",
        "allowed_scalar_paths": scalar_paths,
        "allowed_indexed_templates": [],
        "output": {
            "type": "object",
            "required": ["values"],
            "properties": {
                "values": {
                    "type": "array",
                    "maxItems": len(scalar_paths),
                    "items": {
                        "type": "object",
                        "required": ["path", "value", "evidence_id"],
                        "properties": {
                            "path": {"type": "string", "enum": scalar_paths},
                            "value": {"type": "string"},
                            "evidence_id": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "tables": {"type": "object"},
            },
            "additionalProperties": False,
        },
    }


class Reader(Protocol):
    name: ReaderKind

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None: ...
