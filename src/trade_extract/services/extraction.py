"""Reusable extraction, bounded repair and explicit candidate selection."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from time import perf_counter
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
from trade_extract.schemas.invoice import CommercialInvoice
from trade_extract.schemas.registry import get_schema
from trade_extract.services.checks import run_invoice_checks
from trade_extract.services.digital_bill_of_lading import read_manifest, read_single_container_form
from trade_extract.services.invoice_values import normalize_invoice_field
from trade_extract.services.local_extraction import (
    compact_evidence,
    complete_digital_invoice_text,
    delivery_cargo_description,
    delivery_number_matches_label,
    evidence_lines,
    extract_local,
    reconcile_delivery_measurements,
)
from trade_extract.services.readers import Reader, TextLayerReader, VisionReader
from trade_extract.services.readers.base import (
    INVOICE_TYPES,
    PHASED_TYPES,
    compact_schema,
    compact_value_schema,
    invoice_phase_schema,
    partial_model,
)
from trade_extract.services.readers.paddle import (
    PaddleReader,
    contains,
    validate_evidence,
    verify_ocr_box,
)
from trade_extract.services.readers.text_layer import verify_text_box
from trade_extract.services.routing import route_page
from trade_extract.services.table_layout import DigitalGrid, digital_grids
from trade_extract.services.tables import (
    build_inventory,
    complete_wrapped_descriptions,
    digital_cell_candidates,
    mismatched_table_columns,
    native_crops,
    reconcile,
)

logger = logging.getLogger(__name__)

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
    progress: Callable[[str], None] | None = None,
    _structure_pass: int = 0,
    _provider_calls: list[int] | None = None,
) -> ExtractionOutcome:
    if progress:
        progress("reading")
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
    phased = document_type in PHASED_TYPES
    needs_structure = phased and bool(spec.tables) and document_type != "verification_memo"
    invoice = document_type in INVOICE_TYPES
    stage_prefix = "invoice_" if invoice else document_type + "_"
    delivery_order = document_type == "delivery_order"
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
    manifest = None
    image_only_continuations: tuple[int, ...] = ()
    if (
        document_type == "bill_of_lading"
        and settings.provider == "local"
        and not settings.llm_extraction_priority
        and not unreadable_errors
        and not any(page.text.strip() for page in usable[1:])
    ):
        manifest = read_single_container_form(
            usable[0], source.document_hash, settings.ocr_max_rows
        )
        if manifest:
            image_only_continuations = tuple(page.page_index for page in usable[1:])
    single_container_form = manifest is not None
    if (
        manifest is None
        and document_type == "bill_of_lading"
        and len(pages) == 1
        and route_map[usable[0].page_index].readers == (ReaderKind.TEXT_LAYER,)
        and usable[0].vision_ready
        and ReaderKind.VISION in available
    ):
        manifest = read_manifest(usable[0], source.document_hash, settings.ocr_max_rows)
    paddle = next((r for r in configured if isinstance(r, PaddleReader)), None)
    if paddle is not None:
        paddle = PaddleReader(settings, backend=paddle.backend, raw_ocr_cache=paddle.raw_ocr_cache)
        configured = tuple(paddle if isinstance(r, PaddleReader) else r for r in configured)
    prepared = []
    digital_layouts: dict[int, tuple[DigitalGrid, ...]] = {}
    required_columns = {
        name: {
            field.path.split(".", 1)[1] for field in spec.model.field_set.required_row_specs(name)
        }
        for name in spec.tables
    }
    for p in usable:
        if p.ocr is not None:
            try:
                validate_evidence(p.ocr, p, settings)
            except ValueError:
                return outcome(
                    "extraction_failed", errors=("ocr_cached_evidence_invalid", *unreadable_errors)
                )
        digital_ready = manifest is not None
        if (
            not digital_ready
            and paddle is not None
            and needs_structure
            and p.ocr is None
            and route_map[p.page_index].readers == (ReaderKind.TEXT_LAYER,)
        ):
            started = perf_counter()
            layouts = digital_grids(p, spec, settings.ocr_max_rows + 1)
            digital_layouts[p.page_index] = layouts
            digital_ready = (
                {layout.name for layout in layouts} == spec.tables
                and sum(len(layout.rows) for layout in layouts)
                < settings.routing_dense_row_threshold
                and all(
                    sum(column is not None for column in layout.columns) >= 3
                    and required_columns[layout.name].issubset(layout.columns)
                    for layout in layouts
                )
            )
            logger.info(
                "digital_table_preflight page=%d duration_ms=%.1f skip_ocr=%s",
                p.page_index,
                (perf_counter() - started) * 1000,
                digital_ready,
            )
        if digital_ready and p.page_index not in image_only_continuations:
            diagnostics.append(f"page:{p.page_index}:digital_tables_without_ocr")
        if (
            paddle is not None
            and not digital_ready
            and (p.ocr is None or needs_structure and p.ocr.structure_status == "unavailable")
            and (ReaderKind.PADDLE in route_map[p.page_index].readers or needs_structure)
        ):
            started = perf_counter()
            require_structure = needs_structure or route_map[p.page_index].table_required
            evidence = paddle.inspect(p, require_structure=require_structure)
            logger.info(
                "page_ocr page=%d duration_ms=%.1f structure=%s status=%s",
                p.page_index,
                (perf_counter() - started) * 1000,
                require_structure,
                evidence.status,
            )
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
    lines = evidence_lines(usable, preserve_text_order=delivery_order)
    if progress:
        progress("extracting")
    local_candidates = extract_local(document_type, spec, source.document_hash, lines)
    if invoice:
        local_candidates = tuple(
            replace(candidate, field=normalize_invoice_field(candidate.field_path, candidate.field))
            if candidate.field.value_type == ValueType.DECIMAL
            and not candidate.field_path.endswith(".tax_rate")
            else candidate
            for candidate in local_candidates
        )
    if manifest:
        local_candidates += manifest.candidates
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

    if delivery_order:
        request = replace(
            request,
            evidence=request.evidence
            + "\nDelivery order numbers must be explicitly printed beside D/O No., "
            "DO No., or Delivery Order Number. If that field is blank, omit it. "
            "HBL and MBL labels are bill of lading references. When both are printed, "
            "use the MBL for bill_of_lading.number. During header extraction return "
            "an empty tables object. Cite the consignee "
            "from its own field even if the notify party has the same name. "
            "For containers, use one row per printed container and cite every stated cell. "
            "Use gross_weight_kg only if kilograms are stated; otherwise use gross_weight. "
            "Volume is separate from gross weight. Use a stated terminal name only for "
            "release.terminal, and a named signer only for signature.name. "
            "Capture cargo.description from the Description of Goods column. When the "
            "description wraps across evidence lines, cite the longest exact goods phrase "
            "that fits one evidence line.",
        )

    def table_columns(name: str):
        mapped = {key for row in inventory.rows if row.table_name == name for key, _ in row.columns}
        table = next(table for table in spec.model.table_specs if table.name == name)
        return [
            column for column in spec.model.field_set.columns_for(table) if column.key in mapped
        ]

    def retain_partial(error_codes: tuple[str, ...]) -> ExtractionOutcome | None:
        if not candidates or unreadable_errors:
            return None
        merged_partial = merge_candidates(candidates)
        partial_tables = {}
        if phased:
            for name in spec.tables:
                expected = {r.row_index for r in inventory.rows if r.table_name == name}
                emitted = {
                    int(m.group(1))
                    for path in merged_partial
                    if (m := re.match(re.escape(name) + r"\[(\d+)\]", path))
                }
                if expected or emitted:
                    partial_tables[name] = TableMeta(
                        columns=table_columns(name),
                        row_count_stated=len(expected or emitted),
                        row_count_extracted=len(emitted),
                        unresolved_rows=sorted(expected - emitted),
                    )
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
                    "tables": partial_tables,
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
    if manifest and not single_container_form:
        # Form labels and logos can be raster-only even when every row is digital.
        routes = tuple(
            replace(
                route,
                readers=(ReaderKind.TEXT_LAYER, ReaderKind.VISION),
                reasons=(*route.reasons, "digital_manifest_with_visual_header"),
            )
            for route in routes
        )
    route_map = {r.page_index: r for r in routes}
    required = {r.page_index for r in routes if r.table_required and spec.tables}
    if needs_structure:
        required |= set(page_map)
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
    inventory = (
        manifest.inventory
        if manifest
        else build_inventory(usable, required, settings, spec, digital_layouts=digital_layouts)
    )
    if invoice:
        digital_candidates = digital_cell_candidates(usable, inventory, spec, source.document_hash)
        local_candidates += digital_candidates
        candidates.extend(digital_candidates)
        diagnostics.extend(f"local:{candidate.field_path}" for candidate in digital_candidates)
    if (
        document_type == "commercial_invoice"
        and source.content_type == "application/pdf"
        and source.page_start == source.page_end
        and len(usable) == 1
        and not unreadable_errors
        and not settings.llm_extraction_priority
        and digital_layouts.get(usable[0].page_index)
        and (required_scalars | {"totals.tax_amount"}) <= local_paths
        and inventory.rows
        and not inventory.diagnostics
        and all(
            row.table_name == "line_items"
            and row.region_id == "digital-line_items"
            and required_columns["line_items"].issubset(dict(row.columns))
            for row in inventory.rows
        )
    ):
        fields = merge_candidates(local_candidates, select=select)
        tables = {
            "line_items": TableMeta(
                columns=table_columns("line_items"),
                row_count_stated=len(inventory.rows),
                row_count_extracted=len(inventory.rows),
            )
        }
        if (
            all(
                f"line_items[{row.row_index}].{key}" in fields
                for row in inventory.rows
                for key, _ in row.columns
            )
            and not reconcile(inventory, fields, tables)
            and complete_digital_invoice_text(lines, fields)
        ):
            try:
                local_document = CommercialInvoice.model_validate(
                    {
                        "segment_id": segment_id,
                        "status": DocumentStatus.EXTRACTED,
                        "source": source,
                        "fields": fields,
                        "tables": tables,
                        "extension": {},
                    }
                )
            except ValidationError:
                diagnostics.append("local_schema_incomplete")
            else:
                if _comparison(fields["totals.line_extension_amount"]) == _comparison(
                    fields["totals.tax_exclusive_amount"]
                ) and all(check.status != "fail" for check in run_invoice_checks(local_document)):
                    diagnostics.append("provider_skipped_local_complete_invoice")
                    return outcome(
                        "extracted", document=local_document, candidates=local_candidates
                    )
    if manifest and settings.provider == "local" and not settings.llm_extraction_priority:
        missing = sorted(required_scalars - local_paths)
        partial = retain_partial(
            (
                "digital_manifest_header_review",
                *(f"missing_required:{path}" for path in missing),
                *(
                    f"page:{index}:image_only_continuation_requires_review"
                    for index in image_only_continuations
                ),
            )
        )
        if partial is not None:
            return partial
    if phased:
        inventory = replace(
            inventory, rows=tuple(r for r in inventory.rows if r.table_name in spec.tables)
        )
        if inventory.rows:
            # A validated empty grid inventory on a companion/header page is a
            # real inspection, not evidence that the segment has zero rows.
            inventory = replace(
                inventory,
                diagnostics=tuple(
                    d
                    for d in inventory.diagnostics
                    if not d.endswith(":independent_table_not_detected")
                ),
            )
    if not manifest:
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
                        "columns": {key: box.model_dump() for key, box in r.columns},
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
    if (
        spec.tables
        and not phased
        and any(
            r.page_index not in vision_pages for r in inventory.rows if r.page_index in required
        )
    ):
        return outcome(
            "extraction_failed", errors=("table_native_reader_unavailable", *unreadable_errors)
        )
    crop_inventory = replace(
        inventory,
        rows=() if manifest else tuple(r for r in inventory.rows if r.page_index in vision_pages),
    )
    crop_errors: list[str] = []
    try:
        crops = native_crops(usable, crop_inventory, settings)
    except ValueError as exc:
        if phased:
            crops = ()
            diagnostics.append(str(exc))
            if crop_inventory.rows:
                crop_errors.append(str(exc))
        else:
            return outcome(
                "extraction_failed",
                errors=(str(exc), *unreadable_errors),
                routes=routes,
                inventory=inventory,
            )
    documents: list[DocumentEnvelope] = []
    # Extraction stages are independently repairable and retain completed evidence.
    # Physical inventory supplies global indices; absent structure gets one bounded
    # inspection stage, whose evidence can never pass independent reconciliation.
    jobs: list[tuple[Reader, str | None, tuple[int, ...]]] = []
    preparation_errors: list[str] = crop_errors
    for reader in configured:
        if manifest:
            if reader.name == ReaderKind.VISION:
                jobs.append((reader, "header_party", ()))
            continue
        if phased:
            # Reserve room for optional columns and full vision provenance as
            # well as attached images. Reuse existing output/image settings.
            size = max(
                1,
                min(
                    32,
                    settings.provider_max_images - 1,
                    settings.provider_max_output_tokens // 1200,
                ),
            )
            jobs.append((reader, "header_party", ()))
            for table in sorted(spec.tables):
                indices = tuple(r.row_index for r in inventory.rows if r.table_name == table)
                batches = [indices[i : i + size] for i in range(0, len(indices), size)] or [
                    tuple(range(size))
                ]
                if len(batches) > settings.table_max_batches:
                    preparation_errors.append("table_provider_batch_budget_exhausted")
                    batches = batches[: settings.table_max_batches]
                jobs.extend((reader, table, batch) for batch in batches)
            if invoice:
                jobs.append((reader, "totals_tax", ()))
        else:
            jobs.append((reader, None, ()))
    stage_errors: list[str] = preparation_errors
    provider_table_counts: dict[str, set[int]] = defaultdict(set)
    if phased and len(re.findall(r"^p\d+l\d+\|p=", request.evidence, re.MULTILINE)) < len(lines):
        stage_errors.append("evidence_index_truncated")
    for reader, phase, batch_rows in jobs:
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
        if phase in spec.tables:
            batch_pages = {
                row.page_index
                for row in inventory.rows
                if row.table_name == phase and row.row_index in batch_rows
            }
            if batch_pages:
                eligible = tuple(p for p in eligible if p.page_index in batch_pages)
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
                crops=tuple(
                    c
                    for c in crops
                    if c.row.page_index in evidence_pages
                    and (
                        phase is None or phase == c.row.table_name and c.row.row_index in batch_rows
                    )
                ),
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
            if isinstance(reader, VisionReader)
            else request
        )
        if phase is not None:
            phase_contract = invoice_phase_schema(document_type, phase, rows=batch_rows)
            if settings.provider in {"local", "groq"} and not isinstance(reader, VisionReader):
                phase_paths = set(phase_contract["allowed_scalar_paths"])
                if not settings.llm_extraction_priority:
                    phase_paths.difference_update(c.field_path for c in local_candidates)
                allowed = sorted(phase_paths)
                phase_contract["allowed_scalar_paths"] = allowed
                compact_values = phase_contract["output"]["properties"]["values"]
                compact_values["maxItems"] = len(allowed)
                compact_values["items"]["properties"]["path"]["enum"] = allowed
                if not allowed:
                    continue
            if isinstance(reader, VisionReader):
                phase_schema = partial_model(document_type).json_schema()
                allowed = phase_contract["allowed_scalar_paths"]
                original_fields = phase_schema["properties"]["fields"]
                field_schema = next(iter(original_fields["properties"].values()))
                phase_schema["properties"]["fields"] = {
                    "type": "object",
                    "properties": {
                        path: {
                            **field_schema,
                            "properties": {
                                "value_type": {
                                    "const": spec.model.field_set.by_template[
                                        re.sub(r"\[\d+\]", "[]", path)
                                    ].value_type.value
                                }
                            },
                        }
                        for path in allowed
                    },
                    "additionalProperties": False,
                }
                phase_schema["allowed_phase_paths"] = allowed
                if manifest:
                    phase_schema["properties"]["tables"] = {"type": "object", "maxProperties": 0}
            else:
                phase_schema = phase_contract
            current = replace(
                current,
                schema_json=phase_schema,
                stage=stage_prefix + phase,
                evidence=current.evidence
                + "\nExtraction phase: "
                + phase
                + ". Extract all stated permitted fields, including optional fields. "
                + "Preserve printed date/currency strings. Never calculate missing values. "
                + "Only use allowed global row indices. Keep different tables and arrays separate. "
                + "When a column has a supplied source box, cite values from that box only.",
            )
            if manifest:
                current = replace(
                    current,
                    max_output_tokens=min(current.max_output_tokens or 4096, 4096),
                    evidence=current.evidence
                    + "\nThe cargo and container rows are already read locally. Return an empty "
                    "tables object. Read the form labels and carrier logo from the image, and "
                    "cite each value itself. Leave blank signature and place-of-issue "
                    "fields empty.",
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
                if phase is not None:
                    allowed_paths = set(phase_contract["allowed_scalar_paths"])
                    if set(doc.fields) - allowed_paths or doc.extension:
                        raise ExtractionGuard("out_of_phase_path")
                    if manifest and doc.tables:
                        raise ExtractionGuard("out_of_phase_table")
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
                if delivery_order and (number := doc.fields.get("delivery_order.number")):
                    label_lines = lines
                    if number.provenance.reader != ReaderKind.TEXT_LAYER:
                        label_lines = (
                            evidence_lines(
                                tuple(replace(p, characters=()) for p in eligible if p.ocr)
                            )
                            or lines
                        )
                    if not delivery_number_matches_label(number, label_lines):
                        del doc.fields["delivery_order.number"]
                        stage_errors.append("delivery_order.number:reference_label_mismatch")
                if (
                    delivery_order
                    and phase == "containers"
                    and reader.name == ReaderKind.TEXT_LAYER
                ):
                    reconcile_delivery_measurements(doc.fields, lines)
                for path in mismatched_table_columns(inventory, doc.fields):
                    del doc.fields[path]
                    stage_errors.append(f"{path}:column_geometry_mismatch")
                if phase in spec.tables and not delivery_order:
                    cell_lines = (
                        evidence_lines(tuple(replace(page, characters=()) for page in eligible))
                        if reader.name == ReaderKind.PADDLE
                        else evidence_lines(eligible, preserve_text_order=True)
                    )
                    complete_wrapped_descriptions(inventory, doc.fields, cell_lines)
                # Only accepted evidence may contribute completeness metadata.
                if phased and result.finish_reason in {"length", "max_tokens"}:
                    stage_errors.append("stage_output_truncated")
                if phase is not None:
                    for name, meta in doc.tables.items():
                        provider_table_counts[name].add(meta.row_count_stated)
                        if len(provider_table_counts[name]) > 1:
                            stage_errors.append("table_metadata_disagreement")
                        if meta.truncated:
                            stage_errors.append("table:" + name + ":provider_truncated")
                        count = sum(r.table_name == name for r in inventory.rows)
                        if count and meta.row_count_stated != count:
                            stage_errors.append("table:" + name + ":provider_count_disagreement")
                    if any(
                        "[31]" in path and path.split("[", 1)[0] not in spec.tables
                        for path in doc.fields
                    ):
                        stage_errors.append(stage_prefix + "array_bound_reached")
                if invoice:
                    for path, field in doc.fields.items():
                        if field.value_type in {
                            ValueType.DECIMAL,
                            ValueType.DATE,
                        } and not path.endswith(".tax_rate"):
                            doc.fields[path] = normalize_invoice_field(path, field)
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
                if phased:
                    stage_errors.append(exc.code)
                    diagnostics.append("stage:" + str(phase) + ":" + exc.code)
                    break
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
                    if phased:
                        stage_errors.extend(errors)
                        diagnostics.extend("stage:" + str(phase) + ":" + e for e in errors)
                        break
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
        if phased:
            partial = retain_partial(tuple(stage_errors) or ("no_reader_evidence",))
            if partial is not None:
                return partial
        return outcome(
            "unreadable" if unreadable_errors else "extraction_failed",
            errors=(*tuple(dict.fromkeys(stage_errors)), "no_reader_evidence", *unreadable_errors),
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
    if delivery_order:
        cargo = delivery_cargo_description(merged, lines)
        if cargo is not None:
            candidates = [
                candidate for candidate in candidates if candidate.field_path != "cargo.description"
            ]
            candidates.append(FieldCandidate("cargo.description", cargo))
            merged["cargo.description"] = cargo
            container_paths = [
                path for path in merged if re.fullmatch(r"containers\[\d+\]\.number", path)
            ]
            if len(container_paths) == 1:
                description_path = container_paths[0].removesuffix(".number") + ".description"
                if not mismatched_table_columns(inventory, {description_path: cargo}):
                    candidates = [
                        candidate
                        for candidate in candidates
                        if candidate.field_path != description_path
                    ]
                    candidates.append(FieldCandidate(description_path, cargo))
                    merged[description_path] = cargo
        place = merged.get("release.place_of_delivery")
        terminal = merged.get("release.terminal")
        if (
            place
            and terminal
            and place.value == terminal.value
            and not any(re.search(r"\bterminal\s*(?:name)?\s*:", line.text, re.I) for line in lines)
        ):
            candidates = [
                candidate for candidate in candidates if candidate.field_path != "release.terminal"
            ]
            del merged["release.terminal"]
    # Combine complementary eligible subsets before segment-wide reconciliation.
    tables = {}
    for doc in documents:
        if phased:
            continue  # Counts belong to independent physical inventory, never provider guesses.
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
    if phased:
        for name in sorted(spec.tables):
            expected = {r.row_index for r in inventory.rows if r.table_name == name}
            emitted = {
                int(match.group(1))
                for path in merged
                if (match := re.match(re.escape(name) + r"\[(\d+)\]", path))
            }
            if expected or emitted:
                tables[name] = TableMeta(
                    columns=table_columns(name),
                    row_count_stated=len(expected or emitted),
                    row_count_extracted=len(emitted),
                    unresolved_rows=sorted(expected - emitted),
                )
            if expected - emitted:
                stage_errors.append(stage_prefix + "unresolved_" + name)
    # Returned tables on digital pages require independent structure too.
    grid_names = spec.tables
    if document_type == "verification_memo" and not any(
        row.table_name == "findings" for row in inventory.rows
    ):
        grid_names = spec.tables - {"findings"}
    table_pages = {
        f.provenance.page_index
        for path, f in merged.items()
        if (m := re.match(r"([a-z_]+)\[", path)) and m.group(1) in grid_names
    }
    newly_required = table_pages - required
    if grid_names.intersection(tables) and not table_pages:
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
                and match.group(1) in (grid_names if phased else tables)
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
        rerun = extract(
            provider,
            document_type=document_type,
            source=source,
            pages=rerun_pages,
            segment_id=segment_id,
            readers=configured,
            max_repairs=max_repairs,
            select=select,
            settings=settings,
            progress=progress,
            _structure_pass=_structure_pass + 1,
            _provider_calls=_provider_calls,
        )
        if rerun.document is None:
            partial = retain_partial(rerun.errors)
            if partial is not None:
                return partial
        elif phased:
            # Structural discovery must not erase earlier validated header/cell
            # evidence or disagreements when the bounded native pass is partial.
            mapped = []
            unmapped = []
            for candidate in candidates:
                match = re.fullmatch(r"([a-z_]+)\[\d+\](\..+)", candidate.field_path)
                if match is None or match.group(1) not in spec.tables:
                    mapped.append(candidate)
                    continue
                provenance = candidate.field.provenance
                rows = [
                    row
                    for row in rerun.inventory.rows
                    if row.table_name == match.group(1)
                    and row.page_index == provenance.page_index
                    and contains(row.bbox, provenance.bbox)
                ]
                if len(rows) == 1:
                    path = f"{match.group(1)}[{rows[0].row_index}]" + match.group(2)
                    if mismatched_table_columns(rerun.inventory, {path: candidate.field}):
                        unmapped.append(candidate)
                    else:
                        mapped.append(replace(candidate, field_path=path))
                else:
                    unmapped.append(candidate)
            combined = (*mapped, *rerun.candidates)
            retained = merge_candidates(combined, select=select)
            payload = rerun.document.model_dump()
            payload.update(
                fields={p: f for p, f in retained.items() if not p.startswith("extension.")},
                extension={p: f for p, f in retained.items() if p.startswith("extension.")},
            )
            coverage = reconcile(rerun.inventory, retained, rerun.document.tables)
            if coverage or unmapped:
                payload["status"] = DocumentStatus.REVIEW_REQUIRED
                rerun = replace(
                    rerun,
                    errors=(
                        *rerun.errors,
                        *coverage,
                        *(("pre_discovery_row_unmapped",) if unmapped else ()),
                    ),
                )
            model = (
                partial_model(document_type)
                if payload["status"] == DocumentStatus.REVIEW_REQUIRED
                else spec.model
            )
            try:
                retained_document = model.model_validate(payload)
            except ValidationError:
                payload["status"] = DocumentStatus.REVIEW_REQUIRED
                retained_document = partial_model(document_type).model_validate(payload)
                rerun = replace(rerun, errors=(*rerun.errors, "merged_schema_invalid"))
            return replace(
                rerun,
                document=retained_document,
                candidates=(*combined, *unmapped),
                diagnostics=tuple(dict.fromkeys((*diagnostics, *rerun.diagnostics))),
            )
        return rerun
    if newly_required:
        inventory = build_inventory(
            tuple(page_map.values()), required | newly_required, settings, spec
        )
        # Such rows have not been read at native resolution in this request.
        if inventory.rows and not crops:
            diagnostics.append("table_native_crops_required")
    physical_tables = {name: meta for name, meta in tables.items() if name in grid_names}
    coverage = (
        reconcile(inventory, merged, physical_tables)
        if tables or inventory.rows or required
        else inventory.diagnostics
    )
    if coverage or "table_native_crops_required" in diagnostics:
        if phased:
            partial = retain_partial((*coverage, *stage_errors, *diagnostics))
            if partial is not None:
                return partial
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
        if phased and stage_errors:
            partial = retain_partial(tuple(stage_errors))
            if partial is not None:
                return partial
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
