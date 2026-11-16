import re
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from typing import Any, ClassVar, Protocol

from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import ReaderKind
from trade_extract.domain.readers import PageInput
from trade_extract.providers.base import ModelProvider
from trade_extract.schemas.base import DocumentEnvelope, FieldSet
from trade_extract.schemas.registry import SUPPORTED_DOCUMENT_TYPES, get_schema

INVOICE_TYPES = frozenset({"commercial_invoice", "proforma_invoice"})
PHASED_TYPES = SUPPORTED_DOCUMENT_TYPES


def _partial_tables(self, *, field_set: FieldSet) -> None:
    """Stage evidence is not a completeness claim; final models still enforce it."""
    declared = {spec.name for spec in self.table_specs}
    if set(self.tables) - declared:
        raise ValueError("unknown_partial_table")
    for path in self.fields:
        if any(len(i) > 1 and i.startswith("0") for i in re.findall(r"\[(\d+)\]", path)):
            raise ValueError("noncanonical_partial_index")


@lru_cache(maxsize=7)
def partial_model(document_type: str) -> type[DocumentEnvelope]:
    """Validate supplied scalar evidence; document completeness is checked after merge."""
    model = get_schema(document_type).model
    fields = FieldSet(
        tuple(
            replace(s, required=False) if not s.indexed or document_type in PHASED_TYPES else s
            for s in model.field_set.specs
        )
    )
    return type(
        "Partial" + model.__name__,
        (model,),
        {
            "__annotations__": {"field_set": ClassVar[FieldSet]},
            "field_set": fields,
            **({"_validate_tables": _partial_tables} if document_type in PHASED_TYPES else {}),
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


def compact_value_schema(document_type: str, resolved_paths: frozenset[str]) -> dict[str, Any]:
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


def invoice_phase_schema(
    document_type: str,
    phase: str,
    *,
    rows: tuple[int, ...] = (),
    array_limit: int = 32,
) -> dict[str, Any]:
    """Finite concrete paths make row and auxiliary-array budgets enforceable locally."""
    if document_type not in PHASED_TYPES:
        raise ValueError("unsupported_phased_type")
    schema_spec = get_schema(document_type)
    fields = schema_spec.model.field_set
    table_prefixes = tuple(f"{name}[]" for name in sorted(schema_spec.tables))
    paths: list[str] = []
    for spec in fields.specs:
        if phase in schema_spec.tables:
            if spec.path.startswith(f"{phase}[]"):
                for row in rows:
                    path = spec.path.replace("[]", f"[{row}]", 1)
                    if "[]" in path:
                        paths.extend(path.replace("[]", f"[{i}]", 1) for i in range(array_limit))
                    else:
                        paths.append(path)
        elif phase == "totals_tax":
            if spec.path.startswith("totals."):
                paths.append(spec.path)
            elif spec.path.startswith("tax_breakdown[]"):
                paths.extend(spec.path.replace("[]", f"[{i}]", 1) for i in range(array_limit))
        elif phase == "header_party":
            if not spec.path.startswith((*table_prefixes, "totals.", "tax_breakdown[]")):
                if spec.indexed:
                    paths.extend(spec.path.replace("[]", f"[{i}]", 1) for i in range(array_limit))
                else:
                    paths.append(spec.path)
        else:
            raise ValueError("unknown_extraction_phase")
    schema = compact_value_schema(document_type, frozenset())
    schema["allowed_scalar_paths"] = paths
    schema["phase"] = phase
    schema["allowed_row_indices"] = list(rows)
    schema["array_limit"] = array_limit
    values = schema["output"]["properties"]["values"]
    values["maxItems"] = len(paths)
    values["items"]["properties"]["path"]["enum"] = paths
    return schema


class Reader(Protocol):
    name: ReaderKind

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None: ...
