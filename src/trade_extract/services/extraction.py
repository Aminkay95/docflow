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

from trade_extract.config import Settings
from trade_extract.domain.enums import DocumentStatus
from trade_extract.domain.fields import Agreement, Field, FieldAlternative, ValueType
from trade_extract.domain.hashing import stable_json
from trade_extract.domain.inference import ExtractionOutcome, InferenceRequest, ProviderError
from trade_extract.domain.provenance import BBox, Provenance, ReaderKind
from trade_extract.domain.readers import FieldCandidate, PageFeatures, PageInput, TableInventory
from trade_extract.providers.base import ModelProvider
from trade_extract.schemas.base import DocumentEnvelope, EnvelopeSource, TableMeta
from trade_extract.schemas.registry import get_schema
from trade_extract.services.local_extraction import compact_evidence, evidence_lines, extract_local
from trade_extract.services.readers import Reader, TextLayerReader, VisionReader
from trade_extract.services.readers.base import compact_schema, compact_value_schema, partial_model
from trade_extract.services.readers.paddle import (
    PaddleReader,
    contains,
    validate_evidence,
    verify_ocr_box,
)
from trade_extract.services.readers.text_layer import verify_text_box
from trade_extract.services.routing import route_page
from trade_extract.services.tables import build_inventory, native_crops, reconcile

SelectionPolicy = Callable[[tuple[FieldCandidate, ...]], FieldCandidate]


class _BudgetedProvider:
    def __init__(self, provider: ModelProvider, counter: list[int], limit: int) -> None:
        self._provider = provider
        self._counter = counter
        self._limit = limit
        self.name = getattr(provider, "name", "provider")

    def _claim(self) -> None:
        if self._counter[0] >= self._limit:
            raise ProviderError("provider_call_budget_exhausted")
        self._counter[0] += 1

    def text_reason(self, request: InferenceRequest):
        self._claim()
        return self._provider.text_reason(request)

    def vision_extract(self, request: InferenceRequest):
        self._claim()
        return self._provider.vision_extract(request)


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
    max_repairs: int | None = None,
    select: SelectionPolicy = stable_selection,
    settings: Settings | None = None,
    _structure_pass: int = 0,
    _provider_calls: list[int] | None = None,
) -> ExtractionOutcome:
    routes: tuple[Any, ...] = ()
    inventory = TableInventory()
    diagnostics: list[str] = []

    def outcome(*args: Any, **kwargs: Any) -> ExtractionOutcome:
        kwargs.setdefault("routes", routes)
        kwargs.setdefault("inventory", inventory)
        kwargs.setdefault("diagnostics", tuple(diagnostics))
        return ExtractionOutcome(*args, **kwargs)

    settings = settings or Settings()
    if max_repairs is None:
        max_repairs = min(settings.provider_max_repairs, 1)
    if not 0 <= max_repairs <= 1:
        raise ValueError("max_repairs must be between zero and one")
    if _provider_calls is None:
        _provider_calls = [0]
        provider = _BudgetedProvider(
            provider,
            _provider_calls,
            max(1, len(pages)) * settings.provider_max_calls_per_page,
        )
    configured = (
        tuple(readers)
        if readers is not None
        else (TextLayerReader(), PaddleReader(settings), VisionReader())
    )
    available = {r.name for r in configured}
    routes = tuple(route_page(p, settings, available) for p in pages)
    if len(pages) > settings.max_pages_per_file:
        return outcome("extraction_failed", errors=("extraction_page_budget_exhausted",))
    spec = get_schema(document_type)
    usable = tuple(page for page in pages if not page.is_unreadable)
    unreadable_errors = tuple(
        f"page:{page.page_index}:" + reason
        for page in pages
        if page.is_unreadable
        for reason in (page.unreadable_reasons or ("unreadable",))
    )
    if not usable:
        return outcome("unreadable", errors=("no_readable_pages", *unreadable_errors))
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
        schema_json=partial_model(document_type).json_schema(),
        segment_id=segment_id,
        page_count=max(1, len(usable)),
        max_output_tokens=settings.provider_max_output_tokens,
        source_json=source.model_dump(mode="json"),
        evidence=json.dumps(
            {
                "source": source.model_dump(),
                "segment_id": segment_id,
                "readable_pages": sorted(page_map),
            }
        ),
    )
    route_map = {r.page_index: r for r in routes}
    paddle = next((r for r in configured if isinstance(r, PaddleReader)), None)
    if paddle is not None:
        paddle = PaddleReader(settings, backend=paddle.backend, raw_ocr_cache=paddle.raw_ocr_cache)
        configured = tuple(paddle if isinstance(r, PaddleReader) else r for r in configured)
    prepared = []
    for p in usable:
        if p.ocr is not None:
            try:
                validate_evidence(p.ocr, p, settings)
            except ValueError:
                return outcome(
                    "extraction_failed", errors=("ocr_cached_evidence_invalid", *unreadable_errors)
                )
        if (
            paddle is not None
            and p.ocr is None
            and ReaderKind.PADDLE in route_map[p.page_index].readers
        ):
            evidence = paddle.inspect(p, require_structure=route_map[p.page_index].table_required)
            p = replace(p, ocr=evidence)
            diagnostics.extend(f"page:{p.page_index}:{d}" for d in evidence.diagnostics)
        if p.ocr is not None and (
            p.ocr.structure_status == "error"
            or sum(not r.header for t in p.ocr.tables for r in t.rows)
            >= settings.routing_dense_row_threshold
        ):
            p = replace(p, features=replace(p.features or PageFeatures(), dense_table=True))
        prepared.append(p)
    usable = tuple(prepared)
    page_map = {p.page_index: p for p in usable}
    lines = evidence_lines(usable)
    local_candidates = extract_local(document_type, spec, source.document_hash, lines)
    candidates: list[FieldCandidate] = list(local_candidates)
    diagnostics.extend(f"local:{candidate.field_path}" for candidate in local_candidates)
    request = replace(
        request,
        schema_json=compact_value_schema(
            document_type,
            # LLM priority: the provider is asked for every required path so its
            # values compete with local candidates instead of being filtered out
            # by allowed_scalar_paths (disagreement -> DISPUTED -> review).
            frozenset()
            if settings.llm_extraction_priority
            else frozenset(candidate.field_path for candidate in local_candidates),
        ),
        evidence=request.evidence
        + "\nCompact evidence index:\n"
        + compact_evidence(
            lines,
            max_characters=(
                settings.provider_max_evidence_characters_per_page * max(1, len(usable))
            ),
        ),
        stage="unresolved_fields",
    )

    def retain_partial(error_codes: tuple[str, ...]) -> ExtractionOutcome | None:
        if not candidates or unreadable_errors:
            return None
        merged_partial = merge_candidates(candidates)
        try:
            partial = partial_model(document_type).model_validate(
                {
                    "segment_id": segment_id,
                    "document_type": document_type,
                    "schema_version": spec.version,
                    "status": DocumentStatus.REVIEW_REQUIRED,
                    "source": source.model_dump(mode="json"),
                    "fields": {
                        path: field
                        for path, field in merged_partial.items()
                        if not path.startswith("extension.")
                    },
                    "tables": {},
                    "extension": {
                        path: field
                        for path, field in merged_partial.items()
                        if path.startswith("extension.")
                    },
                }
            )
        except ValidationError:
            return None
        diagnostics.append("partial_evidence_retained")
        return outcome(
            "extracted",
            document=partial,
            candidates=tuple(candidates),
            errors=(*error_codes, *unreadable_errors),
        )

    routes = tuple(
        route_page(
            p,
            settings,
            available
            - ({ReaderKind.PADDLE} if p.ocr is not None and p.ocr.status != "ok" else set()),
        )
        for p in (*usable, *(p for p in pages if p.is_unreadable))
    )
    route_map = {r.page_index: r for r in routes}
    required = {r.page_index for r in routes if r.table_required}
    local_paths = {candidate.field_path for candidate in local_candidates}
    required_scalars = set(spec.model.field_set.required_scalar_paths)
    if (
        required_scalars
        and required_scalars <= local_paths
        and not required
        and not unreadable_errors
        # LLM priority: never short-circuit the provider; local candidates
        # still merge as corroboration below (disagreement -> DISPUTED).
        and not settings.llm_extraction_priority
    ):
        local_fields = merge_candidates(local_candidates)
        try:
            local_document = spec.model.model_validate(
                {
                    "segment_id": segment_id,
                    "document_type": document_type,
                    "schema_version": spec.version,
                    "status": DocumentStatus.EXTRACTED,
                    "source": source.model_dump(mode="json"),
                    "fields": local_fields,
                    "tables": {},
                    "extension": {},
                }
            )
        except ValidationError:
            diagnostics.append("local_schema_incomplete")
        else:
            diagnostics.append("provider_skipped_local_complete")
            return outcome(
                "extracted",
                document=local_document,
                candidates=local_candidates,
                errors=tuple(diagnostics),
            )
    inventory = build_inventory(usable, required, settings)
    request = replace(
        request,
        evidence=request.evidence
        + "\nIndependent rows: "
        + json.dumps(
            [
                {
                    "table": r.table_name,
                    "row_index": r.row_index,
                    "page_index": r.page_index,
                    "bbox": r.bbox.model_dump(),
                }
                for r in inventory.rows
            ]
        )
        + "\nUse global row indices. Name every unprovided row in unresolved_rows; "
        + "never shorten stated counts.",
    )
    vision_pages = {
        p.page_index for p in usable if ReaderKind.VISION in route_map[p.page_index].readers
    }
    if any(r.page_index not in vision_pages for r in inventory.rows if r.page_index in required):
        return outcome(
            "extraction_failed", errors=("table_native_reader_unavailable", *unreadable_errors)
        )
    crop_inventory = replace(
        inventory, rows=tuple(r for r in inventory.rows if r.page_index in vision_pages)
    )
    try:
        crops = native_crops(usable, crop_inventory, settings)
    except ValueError as exc:
        return outcome(
            "extraction_failed",
            errors=(str(exc), *unreadable_errors),
            routes=routes,
            inventory=inventory,
        )
    documents: list[DocumentEnvelope] = []
    for reader in configured:
        if isinstance(reader, PaddleReader) and paddle is not None:
            reader = paddle
        eligible = tuple(
            p
            for p in usable
            if reader.name in route_map[p.page_index].readers
            and not (
                reader.name == ReaderKind.PADDLE
                and p.features
                and any(
                    v is True
                    for v in (
                        p.features.photograph,
                        p.features.skew,
                        p.features.stamps,
                        p.features.handwriting,
                    )
                )
            )
        )
        if not eligible:
            continue
        evidence_pages = (
            {
                p.page_index
                for p in eligible
                if (p.image is not None and p.vision_ready)
                if reader.name == ReaderKind.VISION
            }
            if reader.name == ReaderKind.VISION
            else {
                p.page_index
                for p in eligible
                if (
                    bool(p.ocr and p.ocr.spans)
                    if reader.name == ReaderKind.PADDLE
                    else bool(p.characters)
                )
            }
        )
        if isinstance(reader, VisionReader):
            reader = VisionReader(
                crops=tuple(c for c in crops if c.row.page_index in evidence_pages),
                settings=settings,
            )
        current = (
            replace(
                request,
                schema_json=compact_schema(
                    document_type,
                    # LLM priority: vision sees the full contract too; returned
                    # values are never filtered, only the prompt hint changes.
                    frozenset()
                    if settings.llm_extraction_priority
                    else frozenset(candidate.field_path for candidate in local_candidates),
                ),
                stage="vision_or_ocr_document",
            )
            if isinstance(reader, (VisionReader, PaddleReader))
            else request
        )
        base_request = current
        for attempt in range(max_repairs + 1):
            result = None
            try:
                result = reader.read(provider, current, eligible)
                if result is None:
                    break
                raw = (
                    json.loads(result.raw_json)
                    if isinstance(result.raw_json, str)
                    else result.raw_json
                )
                doc = partial_model(document_type).model_validate(raw)
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
                if reader.name == ReaderKind.PADDLE:
                    for path, field in {**doc.fields, **doc.extension}.items():
                        p = page_map[field.provenance.page_index]
                        if not verify_ocr_box(
                            p, field.raw_text or field.value, field.provenance.bbox
                        ) or not verify_ocr_box(p, field.value, field.provenance.bbox):
                            raise ExtractionGuard("ocr_span_geometry_mismatch", path)
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
                partial = retain_partial((exc.code,))
                if partial is not None:
                    return partial
                return outcome(
                    "extraction_failed",
                    candidates=tuple(candidates),
                    errors=(exc.code, *unreadable_errors),
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
                        else "$:" + str(exc)
                        if isinstance(exc, ValueError)
                        and str(exc).startswith("invalid_compact_provider_")
                        else "$:invalid_provider_document",
                    )
                if attempt == max_repairs:
                    partial = retain_partial(errors)
                    if partial is not None:
                        return partial
                    return outcome(
                        "extraction_failed",
                        candidates=tuple(candidates),
                        errors=(*errors, *unreadable_errors),
                    )
                current = replace(
                    base_request,
                    repair_errors=errors,
                    previous_json=(
                        result.repair_json
                        if result is not None and result.repair_json is not None
                        else result.raw_json
                        if result is not None and isinstance(result.raw_json, str)
                        else json.dumps(result.raw_json)
                        if result is not None
                        else None
                    ),
                )
    if not documents:
        return outcome(
            "unreadable" if unreadable_errors else "extraction_failed",
            errors=("no_reader_evidence", *unreadable_errors),
        )
    if unreadable_errors:
        return outcome("unreadable", candidates=tuple(candidates), errors=unreadable_errors)

    def prefer_primary(choices: tuple[FieldCandidate, ...]) -> FieldCandidate:
        primary = tuple(
            c for c in choices if c.reader == route_map[c.field.provenance.page_index].primary
        )
        return stable_selection(primary or choices)

    merged = merge_candidates(
        candidates, select=prefer_primary if select is stable_selection else select
    )
    # Combine complementary eligible subsets before segment-wide reconciliation.
    tables = {}
    for doc in documents:
        for name, meta in doc.tables.items():
            if name not in tables:
                tables[name] = meta
            elif tables[name] != meta:
                existing = tables[name]
                if (
                    existing.row_count_stated != meta.row_count_stated
                    or existing.columns != meta.columns
                ):
                    return outcome(
                        "extraction_failed",
                        candidates=tuple(candidates),
                        errors=("table_metadata_disagreement",),
                    )
                tables[name] = existing.model_copy(
                    update={
                        "row_count_stated": max(existing.row_count_stated, meta.row_count_stated),
                        "unresolved_rows": sorted(
                            set(existing.unresolved_rows) & set(meta.unresolved_rows)
                        ),
                    }
                )
    # Returned tables on digital pages require independent structure too.
    table_pages = {
        f.provenance.page_index for path, f in merged.items() if re.match(r"[a-z_]+\[", path)
    }
    newly_required = table_pages - required
    if tables and not table_pages:
        newly_required |= set(page_map)
    for index in newly_required:
        p = page_map[index]
        if paddle is not None and (p.ocr is None or p.ocr.structure_status == "unavailable"):
            late_evidence = paddle.inspect(p, require_structure=True)
            page_map[index] = replace(p, ocr=late_evidence)
            diagnostics.extend(f"page:{index}:{d}" for d in late_evidence.diagnostics)
    # Establish semantic correspondence from validated provider row citations that
    # actually intersect physical rows. Never concatenate anonymous grids by order.
    associated = False
    for index, page in list(page_map.items()):
        if page.ocr is None:
            continue
        regions = []
        for region in page.ocr.tables:
            names = {
                match.group(1)
                for path, field in merged.items()
                if (match := re.match(r"([a-z_]+)\[", path))
                and match.group(1) in tables
                and field.provenance.page_index == index
                and any(
                    contains(row.bbox, field.provenance.bbox)
                    for row in region.rows
                    if not row.header
                )
            }
            if region.table_name is None and len(names) == 1:
                region = replace(region, table_name=next(iter(names)))
                associated = True
            regions.append(region)
        if tuple(regions) != page.ocr.tables:
            page_map[index] = replace(
                page,
                ocr=replace(page.ocr, tables=tuple(regions)),
                features=replace(page.features or PageFeatures(), dense_table=True),
            )
    late_named = (
        any(p.ocr and p.ocr.tables for i, p in page_map.items() if i in newly_required)
        and not crops
    )
    if associated or (late_named and _structure_pass == 0):
        if _structure_pass >= 1:
            return outcome(
                "extraction_failed", errors=("table_correspondence_unstable", *unreadable_errors)
            )
        # Re-dispatch with established global row identity and native crops. The
        # cached evidence has names now, so this bounded second pass cannot repeat.
        rerun_pages = tuple(
            replace(
                page_map.get(p.page_index, p),
                features=replace(
                    page_map.get(p.page_index, p).features or PageFeatures(), dense_table=True
                ),
            )
            if p.page_index in newly_required
            else page_map.get(p.page_index, p)
            for p in pages
        )
        return extract(
            provider,
            document_type=document_type,
            source=source,
            pages=rerun_pages,
            segment_id=segment_id,
            readers=configured,
            max_repairs=max_repairs,
            select=select,
            settings=settings,
            _structure_pass=_structure_pass + 1,
            _provider_calls=_provider_calls,
        )
    if newly_required:
        inventory = build_inventory(tuple(page_map.values()), required | newly_required, settings)
        # Such rows have not been read at native resolution in this request.
        if inventory.rows and not crops:
            diagnostics.append("table_native_crops_required")
    coverage = (
        reconcile(inventory, merged, tables)
        if tables or inventory.rows or required
        else inventory.diagnostics
    )
    if coverage or "table_native_crops_required" in diagnostics:
        return outcome(
            "extraction_failed",
            candidates=tuple(candidates),
            errors=(*coverage, *diagnostics, *unreadable_errors),
            routes=routes,
            inventory=inventory,
        )
    for name, meta in tables.items():
        count = len(
            {
                int(m.group(1))
                for path in merged
                if (m := re.match(re.escape(name) + r"\[(\d+)\]", path))
            }
        )
        tables[name] = meta.model_copy(update={"row_count_extracted": count})
    payload = documents[0].model_dump()
    payload.update(
        tables=tables,
        status=DocumentStatus.EXTRACTED,
        created_at=None,
        extracted_at=None,
        fields={path: field for path, field in merged.items() if not path.startswith("extension.")},
        extension={path: field for path, field in merged.items() if path.startswith("extension.")},
    )
    try:
        document = spec.model.model_validate(payload)
    except ValidationError:
        partial = retain_partial(("merged_schema_invalid",))
        if partial is not None:
            return partial
        return outcome(
            "extraction_failed", candidates=tuple(candidates), errors=("merged_schema_invalid",)
        )
    return outcome(
        "extracted",
        document=document,
        candidates=tuple(candidates),
        errors=tuple(diagnostics),
        routes=routes,
        inventory=inventory,
    )
