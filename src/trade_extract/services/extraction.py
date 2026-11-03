"""Reusable extraction, bounded repair and explicit candidate selection."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from trade_extract.domain.enums import DocumentStatus
from trade_extract.domain.fields import Agreement, Field, FieldAlternative, ValueType
from trade_extract.domain.hashing import stable_json
from trade_extract.domain.inference import ExtractionOutcome, InferenceRequest, ProviderError
from trade_extract.domain.provenance import BBox, Provenance
from trade_extract.domain.readers import FieldCandidate, PageInput
from trade_extract.providers.base import ModelProvider
from trade_extract.schemas.base import DocumentEnvelope, EnvelopeSource, TableMeta
from trade_extract.schemas.registry import get_schema
from trade_extract.services.readers import Reader, TextLayerReader, VisionReader
from trade_extract.services.readers.text_layer import verify_text_box

SelectionPolicy = Callable[[tuple[FieldCandidate, ...]], FieldCandidate]


class ExtractionGuard(ValueError):
    def __init__(self, code: str, path: str = "$") -> None:
        self.diagnostic = f"{path}:{code}"
        super().__init__(code)


def stable_selection(candidates: tuple[FieldCandidate, ...]) -> FieldCandidate:
    """Default explicit lexical tie-break, independent of input order and confidence."""
    return min(
        candidates,
        key=lambda c: (
            c.reader.value,
            c.field.value,
            c.field.provenance.page_index,
            c.field.provenance.bbox.model_dump_json(),
            stable_json(c.field.model_dump(mode="json")),
        ),
    )


def _comparison(field: Field) -> str | Decimal:
    value = " ".join(field.value.split())
    if field.value_type in (ValueType.DECIMAL, ValueType.INTEGER):
        try:
            parsed = Decimal(value)
            return parsed if parsed.is_finite() else value
        except InvalidOperation:
            return value
    return value.casefold() if field.value_type == ValueType.ENUM else value


def merge_candidates(
    candidates: Sequence[FieldCandidate], *, select: SelectionPolicy = stable_selection
) -> dict[str, Field]:
    groups: dict[str, list[FieldCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.field_path].append(candidate)
    output: dict[str, Field] = {}
    for path, group in sorted(groups.items()):
        choices = tuple(group)
        selected = select(choices)
        if selected not in choices:
            raise ValueError("selection_policy_returned_unknown_candidate")
        values = {_comparison(candidate.field) for candidate in group}
        readers = {candidate.reader for candidate in group}
        agreement = (
            Agreement.DISPUTED
            if len(values) > 1
            else Agreement.UNANIMOUS
            if len(readers) > 1
            else Agreement.SINGLE_READER
        )
        alternatives = (
            [
                FieldAlternative(
                    value=c.field.value,
                    reader=c.reader,
                    confidence=c.field.provenance.confidence,
                    provenance=c.field.provenance,
                )
                for c in sorted(
                    group,
                    key=lambda c: (
                        c.reader.value,
                        c.field.value,
                        stable_json(c.field.model_dump(mode="json")),
                    ),
                )
                if c != selected
            ]
            if agreement == Agreement.DISPUTED
            else []
        )
        output[path] = selected.field.model_copy(
            update={
                "agreement": agreement,
                "alternatives": alternatives,
            }
        )
    return output


def extract(
    provider: ModelProvider,
    *,
    document_type: str,
    source: EnvelopeSource,
    pages: tuple[PageInput, ...],
    segment_id: str | None = None,
    readers: Sequence[Reader] | None = None,
    max_repairs: int = 2,
    select: SelectionPolicy = stable_selection,
) -> ExtractionOutcome:
    if not 0 <= max_repairs <= 2:
        raise ValueError("max_repairs must be between zero and two")
    spec = get_schema(document_type)
    usable = tuple(page for page in pages if not page.unreadable)
    if not usable:
        return ExtractionOutcome("unreadable", errors=("no_readable_pages",))
    page_map = {page.page_index: page for page in usable}
    if len(page_map) != len(usable) or any(
        not source.page_start <= page.page_index <= source.page_end for page in usable
    ):
        raise ValueError("invalid_input_pages")
    if any(page.image is not None and page.image.page_index != page.page_index for page in pages):
        raise ValueError("image_page_index_mismatch")
    request = InferenceRequest(
        document_type=document_type,
        source_hash=source.document_hash,
        schema_json=spec.json_schema(),
        segment_id=segment_id,
        evidence=json.dumps(
            {
                "source": source.model_dump(),
                "segment_id": segment_id,
                "readable_pages": sorted(page_map),
            }
        ),
    )
    candidates: list[FieldCandidate] = []
    documents: list[DocumentEnvelope] = []
    for reader in readers if readers is not None else (TextLayerReader(), VisionReader()):
        evidence_pages = {
            page.page_index
            for page in usable
            if (page.image is not None if reader.name.value == "vision" else bool(page.characters))
        }
        current = request
        for attempt in range(max_repairs + 1):
            result = None
            try:
                result = reader.read(provider, current, usable)
                if result is None:
                    break
                raw = (
                    json.loads(result.raw_json)
                    if isinstance(result.raw_json, str)
                    else result.raw_json
                )
                doc = spec.model.model_validate(raw)
                if doc.source != source or doc.segment_id != segment_id:
                    raise ExtractionGuard("source_identity_mismatch", "source")
                if any(
                    getattr(doc, key) is not None
                    for key in ("document_id", "result_id", "job_id", "job_ref", "file_id")
                ):
                    raise ExtractionGuard("untrusted_document_identity")
                for path, field in {**doc.fields, **doc.extension}.items():
                    for provenance in [
                        field.provenance,
                        *(a.provenance for a in field.alternatives),
                    ]:
                        if (
                            provenance.page_index not in evidence_pages
                            or provenance.reader != reader.name
                        ):
                            raise ExtractionGuard("reader_provenance_mismatch", path)
                    if field.alternatives:
                        raise ExtractionGuard("reader_must_not_merge_candidates", path)
                    if reader.name.value == "text_layer" and not verify_text_box(
                        page_map[field.provenance.page_index],
                        field.raw_text or field.value,
                        field.provenance.bbox,
                    ):
                        raise ExtractionGuard("text_geometry_mismatch", path)
                    if reader.name.value == "text_layer" and not verify_text_box(
                        page_map[field.provenance.page_index], field.value, field.provenance.bbox
                    ):
                        raise ExtractionGuard("value_span_geometry_mismatch", path)
                documents.append(doc)
                candidates.extend(
                    FieldCandidate(
                        path,
                        field.model_copy(
                            update={
                                "provenance": field.provenance.model_copy(
                                    update={"model": result.model}
                                )
                            }
                        ),
                    )
                    for path, field in {**doc.fields, **doc.extension}.items()
                )
                break
            except ProviderError as exc:
                return ExtractionOutcome(
                    "extraction_failed", candidates=tuple(candidates), errors=(exc.code,)
                )
            except (ValidationError, ValueError, TypeError) as exc:
                if isinstance(exc, ExtractionGuard):
                    errors: tuple[str, ...] = (exc.diagnostic,)
                elif isinstance(exc, ValidationError):
                    # Only schema-owned names and legal field paths enter diagnostics.
                    names = (
                        set(DocumentEnvelope.model_fields)
                        | set(Field.model_fields)
                        | set(EnvelopeSource.model_fields)
                        | set(Provenance.model_fields)
                        | set(BBox.model_fields)
                        | set(TableMeta.model_fields)
                    )

                    def location(error: Mapping[str, Any], names: set[str] = names) -> str:
                        if not error["loc"]:
                            # Envelope validators locate missing fields/tables in static
                            # error prose. Admit only registered schema paths, never values.
                            paths = [
                                path
                                for path in re.findall(r"'([^']+)'", error["msg"])
                                if spec.model.field_set.resolve(path) is not None
                                or path in spec.tables
                            ]
                            return ".".join(paths) or "$"
                        return ".".join(
                            str(part)
                            if isinstance(part, int)
                            or part in names
                            or (
                                isinstance(part, str)
                                and spec.model.field_set.resolve(part) is not None
                            )
                            else "<unknown>"
                            for part in error["loc"]
                        )

                    errors = tuple(
                        sorted(
                            {location(error) + ":" + str(error["type"]) for error in exc.errors()}
                        )
                    )
                else:
                    errors = (
                        "$:invalid_json"
                        if isinstance(exc, json.JSONDecodeError)
                        else "$:invalid_provider_document",
                    )
                if attempt == max_repairs:
                    return ExtractionOutcome(
                        "extraction_failed", candidates=tuple(candidates), errors=errors
                    )
                current = replace(
                    request,
                    repair_errors=errors,
                    previous_json=(
                        result.raw_json
                        if result is not None and isinstance(result.raw_json, str)
                        else json.dumps(result.raw_json)
                        if result is not None
                        else None
                    ),
                )
    if not documents:
        return ExtractionOutcome("extraction_failed", errors=("no_reader_evidence",))
    merged = merge_candidates(candidates, select=select)
    # Table metadata is evidence too. Conflicting completeness claims must not be hidden.
    tables = documents[0].tables
    if any(doc.tables != tables for doc in documents[1:]):
        return ExtractionOutcome(
            "extraction_failed",
            candidates=tuple(candidates),
            errors=("table_metadata_disagreement",),
        )
    payload = documents[0].model_dump()
    payload.update(
        status=DocumentStatus.EXTRACTED,
        created_at=None,
        extracted_at=None,
        fields={path: field for path, field in merged.items() if not path.startswith("extension.")},
        extension={path: field for path, field in merged.items() if path.startswith("extension.")},
    )
    try:
        document = spec.model.model_validate(payload)
    except ValidationError:
        return ExtractionOutcome(
            "extraction_failed", candidates=tuple(candidates), errors=("merged_schema_invalid",)
        )
    return ExtractionOutcome("extracted", document=document, candidates=tuple(candidates))
