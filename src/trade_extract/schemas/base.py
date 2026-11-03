"""Declarative document envelope shared by the seven registered types.

A :class:`FieldSet` is data: it lists every legal field path, its stated value type and
whether it is required.  :class:`DocumentEnvelope` consults that data instead of repeating
validation in each document module, so the provider schema, the validator and the public
API all derive from one definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import Field as PField

from trade_extract.domain.enums import DocumentStatus, DocumentType
from trade_extract.domain.fields import Field, ValueType
from trade_extract.domain.hashing import document_hash
from trade_extract.domain.provenance import bare_sha256

_NAME = r"[a-z][a-z0-9_]*"
_SEGMENT = re.compile(rf"^(?P<name>{_NAME})(?:\[(?P<index>\d*)\])?$")
_CONCRETE_INDEX = re.compile(r"\[(\d+)\]")
_TEMPLATE_INDEX = re.compile(r"\[\]")
_EXTENSION_PATTERN = (
    r"^extension\.(?:any|[a-z]{2})\."
    r"[a-z][a-z0-9_]*(?:\[\d+\])?"
    r"(?:\.[a-z][a-z0-9_]*(?:\[\d+\])?)*$"
)
_EXTENSION_KEY = re.compile(_EXTENSION_PATTERN)
_FIELD_REF = {"$ref": "#/$defs/Field"}
_TABLE_META_REF = {"$ref": "#/$defs/TableMeta"}


@dataclass(frozen=True)
class FieldSpec:
    """One legal canonical path, its stated type and whether it must be present."""

    path: str
    value_type: ValueType
    required: bool = False
    label: str | None = None

    def __post_init__(self) -> None:
        parts = parse_template_path(self.path)
        if not parts:
            raise ValueError("a field path must not be empty")
        if any(index not in (None, "") for _, index in parts):
            raise ValueError(
                f"field path '{self.path}' uses a concrete index; field sets declare [] templates"
            )
        if parts[0][0] == "extension":
            raise ValueError(
                f"field path '{self.path}' belongs under 'extension.<country>.*', not the field set"
            )

    @property
    def indexed(self) -> bool:
        return "[]" in self.path

    @property
    def table(self) -> str | None:
        parts = parse_template_path(self.path)
        if parts and parts[0][1] is not None:
            return parts[0][0]
        return None


@dataclass(frozen=True)
class TableColumn:
    """A table column as published to clients."""

    key: str
    label: str


@dataclass(frozen=True)
class TableSpec:
    """A table declared by the registry; row cells live in the ``FieldSet``."""

    name: str
    columns: tuple[TableColumn, ...] = ()

    def __post_init__(self) -> None:
        if re.fullmatch(_NAME, self.name) is None:
            raise ValueError(f"invalid table name '{self.name}'")
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError(f"table '{self.name}' declares duplicate column keys")


class TableMeta(BaseModel):
    """Completeness metadata for one declared table."""

    model_config = ConfigDict(extra="forbid")

    columns: list[TableColumn] = PField(default_factory=list)
    row_count_stated: int = PField(ge=0)
    row_count_extracted: int = PField(ge=0)
    truncated: bool = False
    unresolved_rows: list[Annotated[int, PField(ge=0)]] = PField(default_factory=list)

    @field_validator("unresolved_rows")
    @classmethod
    def _unique_rows(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("unresolved_rows must not repeat a row index")
        return value


class EnvelopeSource(BaseModel):
    """File and segment identity shared by every emitted leaf."""

    model_config = ConfigDict(extra="forbid")

    filename: str = PField(min_length=1)
    content_type: str = PField(min_length=1)
    file_hash: str
    document_hash: str
    page_start: int = PField(ge=0)
    page_end: int = PField(ge=0)
    page_count: int | None = PField(default=None, ge=1)

    @model_validator(mode="after")
    def _consistent(self) -> EnvelopeSource:
        bare_sha256(self.file_hash)
        bare_sha256(self.document_hash)
        if self.page_end < self.page_start:
            raise ValueError(
                f"source.page_end ({self.page_end}) must be greater than or equal to "
                f"source.page_start ({self.page_start})"
            )
        expected = self.page_end - self.page_start + 1
        if self.page_count is not None and self.page_count != expected:
            raise ValueError(
                f"source.page_count ({self.page_count}) must equal the inclusive page range "
                f"size ({expected})"
            )
        return self


@dataclass(frozen=True)
class FieldSet:
    """The complete set of paths one document type may emit."""

    specs: tuple[FieldSpec, ...]
    _by_template: dict[str, FieldSpec] = dataclass_field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for spec in self.specs:
            if spec.path in seen:
                raise ValueError(f"field path '{spec.path}' is declared more than once")
            seen.add(spec.path)
        object.__setattr__(self, "_by_template", {spec.path: spec for spec in self.specs})

    @property
    def fields(self) -> tuple[FieldSpec, ...]:
        """Alias kept for callers that read the set as a list of fields."""

        return self.specs

    @property
    def by_template(self) -> dict[str, FieldSpec]:
        return self._by_template

    def resolve(self, concrete_path: str) -> FieldSpec | None:
        """Resolve a concrete path such as ``line_items[2].quantity`` to its spec."""

        template = _CONCRETE_INDEX.sub("[]", concrete_path)
        return self.by_template.get(template)

    @property
    def scalar_specs(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.specs if not spec.indexed)

    @property
    def indexed_specs(self) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.specs if spec.indexed)

    @property
    def required_scalar_paths(self) -> tuple[str, ...]:
        return tuple(spec.path for spec in self.scalar_specs if spec.required)

    def table_names(self) -> set[str]:
        return {spec.table for spec in self.indexed_specs if spec.table is not None}

    def row_specs(self, table: str) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.indexed_specs if spec.table == table)

    def required_row_specs(self, table: str) -> tuple[FieldSpec, ...]:
        return tuple(spec for spec in self.row_specs(table) if spec.required)

    def columns_for(self, table: TableSpec) -> tuple[TableColumn, ...]:
        if table.columns:
            return table.columns
        columns: list[TableColumn] = []
        for spec in self.row_specs(table.name):
            relative = spec.path.split(".", 1)
            key = relative[1] if len(relative) == 2 else spec.path
            key = key.split(".", 1)[0].replace("[]", "")
            label = spec.label or key.replace("_", " ").capitalize()
            columns.append(TableColumn(key=key, label=label))
        return tuple(columns)

    def json_schema(self, *, definitions: tuple[str, ...] = ("Field",)) -> dict[str, Any]:
        """Build the constrained ``fields`` object used by the provider contract."""

        properties = {spec.path: _typed_field_schema(spec.value_type) for spec in self.scalar_specs}
        patterns = {
            _pattern_for(spec.path): _typed_field_schema(spec.value_type)
            for spec in self.indexed_specs
        }
        return {
            "type": "object",
            "properties": properties,
            "patternProperties": patterns,
            "required": sorted(self.required_scalar_paths),
            "additionalProperties": False,
        }


class DocumentEnvelope(BaseModel):
    """The shared document envelope; subclasses pin type, version, fields and tables."""

    model_config = ConfigDict(extra="forbid")

    field_set: ClassVar[FieldSet]
    table_specs: ClassVar[tuple[TableSpec, ...]] = ()

    document_id: str | None = None
    result_id: str | None = None
    job_id: str | None = None
    job_ref: str | None = None
    file_id: str | None = None
    segment_id: str | None = None
    document_type: str
    document_type_confidence: float | None = PField(default=None, ge=0.0, le=1.0)
    schema_version: str
    status: DocumentStatus | None = None
    source: EnvelopeSource
    fields: dict[str, Field] = PField(default_factory=dict)
    tables: dict[str, TableMeta] = PField(default_factory=dict)
    extension: dict[str, Field] = PField(default_factory=dict)
    created_at: datetime | None = None
    extracted_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _abstract(cls, value: Any) -> Any:
        if cls is DocumentEnvelope:
            raise ValueError(
                "DocumentEnvelope is abstract; validate against a registered document model"
            )
        return value

    @model_validator(mode="after")
    def _validate_envelope(self) -> DocumentEnvelope:
        field_set = self._field_set()
        document_type = _document_type_value(self.document_type)
        self._verify_source_identity(document_type=document_type)
        self._validate_fields(field_set=field_set, document_type=document_type)
        self._validate_tables(field_set=field_set)
        return self

    def _field_set(self) -> FieldSet:
        field_set = getattr(type(self), "field_set", None)
        if not isinstance(field_set, FieldSet):
            raise ValueError(
                f"{type(self).__name__} must declare a ClassVar 'field_set' "
                "so its document paths are validated"
            )
        return field_set

    def _table_specs(self) -> tuple[TableSpec, ...]:
        specs = getattr(type(self), "table_specs", ())
        return tuple(specs)

    def _verify_source_identity(self, *, document_type: str) -> None:
        source = self.source
        expected = document_hash(
            source_file_sha256=bare_sha256(source.file_hash),
            page_start=source.page_start,
            page_end=source.page_end,
            document_type=document_type,
        )
        actual = bare_sha256(source.document_hash)
        if actual != expected:
            raise ValueError(
                "source.document_hash does not match the segment identity derived from "
                "file hash, page range and document type; "
                f"got sha256:{actual}, expected sha256:{expected}"
            )

    def _validate_fields(self, *, field_set: FieldSet, document_type: str) -> None:
        source = self.source
        for path, field in self.fields.items():
            if path.startswith("extension."):
                raise ValueError(
                    f"field '{path}' is an extension path; place it under 'extension', not 'fields'"
                )
            if _TEMPLATE_INDEX.search(path):
                raise ValueError(
                    f"field '{path}' is a template path; emitted fields must use concrete "
                    "zero-based indices such as line_items[0].quantity"
                )
            spec = field_set.resolve(path)
            if spec is None:
                raise ValueError(
                    f"field '{path}' is not declared for document type '{document_type}'; "
                    "unknown detail belongs under extension.any.<name>"
                )
            self._validate_leaf(
                path=path,
                field=field,
                expected_type=spec.value_type,
                required=spec.required,
                source=source,
            )

        for path in field_set.required_scalar_paths:
            if path not in self.fields:
                raise ValueError(f"required field '{path}' is missing")

        for path, field in self.extension.items():
            if _EXTENSION_KEY.match(path) is None:
                raise ValueError(
                    f"extension path '{path}' must match extension.<lowercase ISO 3166-1 "
                    "alpha-2>.* or extension.any.*"
                )
            self._validate_leaf(
                path=path,
                field=field,
                expected_type=ValueType.STRING,
                required=False,
                source=source,
            )

    def _validate_leaf(
        self,
        *,
        path: str,
        field: Field,
        expected_type: ValueType,
        required: bool,
        source: EnvelopeSource,
    ) -> None:
        if field.value_type is not expected_type:
            raise ValueError(
                f"field '{path}' declares value_type '{field.value_type}' but the schema "
                f"expects '{expected_type}'"
            )
        if required and not field.value.strip():
            raise ValueError(f"required field '{path}' must not be empty or whitespace")
        provenance = field.provenance
        if not provenance.within(source.page_start, source.page_end):
            raise ValueError(
                f"field '{path}' provenance page_index {provenance.page_index} is outside "
                f"source page range {source.page_start}..{source.page_end}"
            )
        if not provenance.matches(source.document_hash):
            raise ValueError(
                f"field '{path}' provenance document_hash does not match source.document_hash"
            )
        for alternative in field.alternatives:
            alternative_provenance = alternative.provenance
            if not alternative_provenance.within(source.page_start, source.page_end):
                raise ValueError(
                    f"field '{path}' alternative provenance page_index "
                    f"{alternative_provenance.page_index} is outside source page range "
                    f"{source.page_start}..{source.page_end}"
                )
            if not alternative_provenance.matches(source.document_hash):
                raise ValueError(
                    f"field '{path}' alternative provenance document_hash does not match "
                    "source.document_hash"
                )

    def _validate_tables(self, *, field_set: FieldSet) -> None:
        declared = {spec.name: spec for spec in self._table_specs()}
        all_rows, array_indices = _index_usage(self.fields, self.extension)
        # A top-level array is a table only when the document type declares it.
        table_rows = {name: rows for name, rows in all_rows.items() if name in declared}

        for name in self.tables:
            if name not in declared:
                raise ValueError(f"table '{name}' is not declared for this document type")

        for table, rows in table_rows.items():
            if rows and table not in self.tables:
                raise ValueError(f"table '{table}' has emitted rows but no TableMeta in 'tables'")

        for table, meta in self.tables.items():
            self._validate_table(table=table, meta=meta, rows=table_rows.get(table, set()))

        for table, rows in table_rows.items():
            for row in sorted(rows):
                for spec in field_set.required_row_specs(table):
                    concrete = _with_row(spec.path, row)
                    field = self.fields.get(concrete)
                    if field is None:
                        raise ValueError(
                            f"required field '{concrete}' is missing for table '{table}' row {row}"
                        )
                    if not field.value.strip():
                        raise ValueError(
                            f"required field '{concrete}' must not be empty or whitespace"
                        )

        table_names = set(declared)
        for prefix, indices in array_indices.items():
            if prefix in table_names:
                continue
            if len(indices) != max(indices) + 1:
                raise ValueError(
                    f"array '{prefix}' indices {sorted(indices)} are not contiguous from 0"
                )

    def _validate_table(self, *, table: str, meta: TableMeta, rows: set[int]) -> None:
        if meta.truncated:
            raise ValueError(f"table '{table}' is truncated; a truncated table is never complete")
        if meta.row_count_extracted != len(rows):
            raise ValueError(
                f"table '{table}' declares row_count_extracted={meta.row_count_extracted} "
                f"but {len(rows)} row indices were emitted"
            )
        if rows and max(rows) >= meta.row_count_stated:
            raise ValueError(
                f"table '{table}' emits row index {max(rows)} outside the stated range "
                f"0..{meta.row_count_stated - 1}"
            )
        unresolved = set(meta.unresolved_rows)
        if unresolved and max(unresolved) >= meta.row_count_stated:
            raise ValueError(
                f"table '{table}' names unresolved row {max(unresolved)} outside the stated "
                f"range 0..{meta.row_count_stated - 1}"
            )
        overlap = rows & unresolved
        if overlap:
            raise ValueError(
                f"table '{table}' row indices {sorted(overlap)} are both emitted and unresolved"
            )
        if len(rows) + len(unresolved) != meta.row_count_stated:
            raise ValueError(
                f"table '{table}' has {len(rows)} emitted and {len(unresolved)} unresolved rows, "
                f"but row_count_stated is {meta.row_count_stated}; every stated row must be "
                "either emitted or named in unresolved_rows"
            )

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return the provider-facing JSON Schema for this document type."""

        field_set = getattr(cls, "field_set", None)
        if not isinstance(field_set, FieldSet):
            raise TypeError(f"{cls.__name__} must declare a ClassVar 'field_set'")
        table_specs = tuple(getattr(cls, "table_specs", ()))
        schema = cls.model_json_schema()
        schema["required"] = sorted(set(schema.get("required", [])) | {"fields"})
        properties = schema.setdefault("properties", {})
        properties["fields"] = field_set.json_schema()
        properties["extension"] = {
            "type": "object",
            "patternProperties": {_EXTENSION_PATTERN: _typed_field_schema(ValueType.STRING)},
            "additionalProperties": False,
        }
        properties["tables"] = {
            "type": "object",
            "properties": {spec.name: dict(_TABLE_META_REF) for spec in table_specs},
            "additionalProperties": False,
        }
        return schema


def parse_template_path(path: str) -> tuple[tuple[str, str | None], ...]:
    """Parse a template path, returning ``(name, index)`` where ``index`` is ``""`` for ``[]``.

    Template paths may not contain concrete numeric indices; concrete paths may not contain
    empty brackets.
    """

    if not path:
        raise ValueError("a field path must not be empty")
    parts: list[tuple[str, str | None]] = []
    for segment in path.split("."):
        match = _SEGMENT.fullmatch(segment)
        if match is None:
            raise ValueError(
                f"invalid path segment '{segment}' in '{path}'; expected lowercase names "
                "with optional [] or [<index>]"
            )
        parts.append((match.group("name"), match.group("index")))
    return tuple(parts)


def parse_concrete_path(path: str) -> tuple[tuple[str, int | None], ...]:
    """Parse a concrete emitted path, preserving each numeric index."""

    if not path:
        raise ValueError("a field path must not be empty")
    parts: list[tuple[str, int | None]] = []
    for segment in path.split("."):
        match = _SEGMENT.fullmatch(segment)
        if match is None:
            raise ValueError(f"invalid path segment '{segment}' in '{path}'")
        index = match.group("index")
        if index and len(index) > 1 and index.startswith("0"):
            raise ValueError(f"field '{path}' has a noncanonical index '{index}'; use {int(index)}")
        parts.append((match.group("name"), int(index) if index else None))
    return tuple(parts)


def _index_usage(
    fields: dict[str, Field], extension: dict[str, Field]
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Return table row indices and per-array indices without materialising any range."""

    table_rows: dict[str, set[int]] = {}
    array_indices: dict[str, set[int]] = {}
    for path in [*fields, *extension]:
        parts = parse_concrete_path(path)
        for position, (name, index) in enumerate(parts):
            if index is None:
                continue
            prefix = _array_prefix(parts, position)
            if position == 0:
                table_rows.setdefault(name, set()).add(index)
            array_indices.setdefault(prefix, set()).add(index)
    return table_rows, array_indices


def _array_prefix(parts: tuple[tuple[str, int | None], ...], position: int) -> str:
    rendered: list[str] = []
    for offset, (name, index) in enumerate(parts[: position + 1]):
        if offset == position:
            rendered.append(name)
        elif index is not None:
            rendered.append(f"{name}[{index}]")
        else:
            rendered.append(name)
    return ".".join(rendered)


def _with_row(template: str, row: int) -> str:
    return _TEMPLATE_INDEX.sub(f"[{row}]", template, count=1)


def _pattern_for(template: str) -> str:
    escaped = re.escape(template)
    return "^" + escaped.replace(r"\[\]", r"\[(?!0\d)\d+\]") + "$"


def _typed_field_schema(value_type: ValueType) -> dict[str, Any]:
    return {**_FIELD_REF, "properties": {"value_type": {"const": value_type.value}}}


def _document_type_value(value: DocumentType | str) -> str:
    if isinstance(value, DocumentType):
        return value.value
    return str(value)


def single_literal(annotation: Any) -> str | None:
    """Return the only ``Literal`` value for a pinned field annotation, else ``None``."""

    if get_origin(annotation) is Literal:
        args = get_args(annotation)
        if len(args) == 1 and isinstance(args[0], str):
            return args[0]
    return None
