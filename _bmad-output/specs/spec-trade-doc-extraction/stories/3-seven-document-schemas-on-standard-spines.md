---
title: 'Seven document schemas on standard spines'
type: 'feature'
created: '2026-11-01'
status: 'in-progress'
route: 'dispatch'
review_loop_iteration: 1
baseline_commit: '1bba29a66875ce2a32226d17b66c0921bedd2ca6'
context:
  - 'docs/06-document-schemas.md'
  - 'docs/02-file-structure.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiated">

## Intent

**Problem:** Segments exist, but a document emitted today is still an opaque, unvalidated JSON blob. `schema_version` is never set, nothing prevents a page coordinate from being invented, and there is no definition a prompt, a validator and the published API could share.

**Approach:** Define one declarative document envelope plus seven per-type field sets as Pydantic models behind a registry. The same definition generates the provider's output schema, validates provider output, and documents the public contract. No extraction, persistence or checks in this story.

## Boundaries & Constraints

**Always:** Every emitted leaf is a `Field` carrying provenance (page index, bbox, reader, document hash); a leaf without provenance is a validation failure. Values are the page's characters plus a declared `value_type`; no normalisation, coercion or arithmetic. Exactly seven types; the registry must accept an eighth additively, with no pipeline branch. Field sets come from the standard spines now and are corrected by real documents at the eval harness; a meaning change bumps the version. Schema versions are immutable — additive optional fields may ship within a version. National context is data, never a field name: extensions are namespaced by lowercase ISO 3166-1 alpha-2 (`extension.ke.*`) or `extension.any.*`. The custom-entry field set is provisional and must say so in code.

**Never:** No database tables, migrations, pipeline, provider calls, checks, conflicts or review items. No provider-specific or SQLAlchemy/FastAPI types in `domain` or `schemas`. Never infer or reclassify a document; `unknown` is not a schema.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | Commercial invoice with every required path, each with provenance | Validates; `schema_version` = `invoice.peppol-1.0` | N/A |
| MISSING_REQUIRED | `invoice.number` absent | Rejected | Error names the path and that it is required |
| UNKNOWN_PATH | `invoice.kenya_vat` | Rejected | Error names the path; extensions are the only escape |
| EXTENSION_PATH | `extension.ke.pin`, `extension.any.other` | Accepted | `extension.kenya.pin` rejected |
| BAD_BBOX | `x0 > x1`, or outside 0–1 | Rejected | Error names field and coordinate |
| PAGE_OUT_OF_RANGE | `page_index` outside `source.page_start..page_end` | Rejected | Error names field and range |
| TYPE_MISMATCH | Field declares `integer`, spec expects `decimal` | Rejected | Error names expected and actual |
| TRUNCATED_TABLE | `truncated: true` | Rejected as incomplete | Error says a truncated table is never complete |
| UNRESOLVED_ROWS | Counts differ and `unresolved_rows` names the gap | Accepted, gap preserved | N/A |
| PROFORMA_BL | `bill_of_lading.number` on a proforma | Rejected | Suggests `extension.any.*` |
| UNKNOWN_TYPE | Registry lookup for `unknown` | Rejected | Explicit unsupported-type error |

</frozen-after-approval>

## Code Map

- `docs/06-document-schemas.md` — authoritative envelope, provenance, per-type field tables, registry, validation loop.
- `docs/02-file-structure.md` §2–3 — `schemas/` responsibilities and import boundaries, enforced by test.
- `src/trade_extract/domain/enums.py` — `DocumentType` already includes `packing_list` and `unknown`; do not remove.
- `src/trade_extract/domain/hashing.py::document_hash` — reuse for segment identity, and to *verify* a supplied hash.
- `src/trade_extract/api/schemas/results.py::Document` — `fields`/`extension` are loose maps, `tables` is `dict[str, Any]`, `Source` is a second, weaker source model.
- `src/trade_extract/services/ingest.py::build_result` — produces the payload dicts; keep working unchanged.
- `tests/integration/test_pages_segmentation.py` — asserts `fields is None` and empty `tables`/`extension` on queued documents; keep that behaviour.
- The first derivation was reverted. The Spec Change Log lists what to preserve and what was defective; the Implementation Notes below it are history, not current state.

## Tasks & Acceptance

**Execution:**
- [ ] `src/trade_extract/domain/provenance.py` — `BBox`, `Provenance`, `CoordinateSpace`, `ReaderKind`; bounds, ordering, confidence, page-number consistency, hash syntax.
- [ ] `src/trade_extract/domain/fields.py` — `Field`, `ValueType`, `Agreement`, `FieldAlternative`; provenance required; disputed fields require alternatives, others reject them; an alternative's `reader`/`confidence` must agree with its own provenance.
- [ ] `src/trade_extract/schemas/base.py` — `FieldSpec`, `FieldSet`, `TableSpec`, `TableMeta`, `EnvelopeSource`, `DocumentEnvelope`; spec-driven path, required, provenance and table validation per Design Notes.
- [ ] `src/trade_extract/schemas/common.py` — reusable party, address, shipment and container specs.
- [ ] `src/trade_extract/schemas/{invoice,proforma,bill_of_lading,delivery_order,port_pass,custom_entry,verification_memo}.py` — field sets from `docs/06`; custom entry marked provisional.
- [ ] `src/trade_extract/schemas/registry.py` — `SchemaSpec`, `DOCUMENT_SCHEMAS` for seven types; reject unpinned models and tables disconnected from the field set; `json_schema()` per Design Notes.
- [ ] `src/trade_extract/api/schemas/results.py` — reuse `Field`, `EnvelopeSource` and `TableMeta` instead of parallel weaker models.
- [ ] `docs/06-document-schemas.md`, `docs/08-operations-and-decisions.md` — record source-row indexing, string-only v1 extensions, the full proforma rename, and the packing-list entry as proposed rather than active.
- [ ] `tests/unit/test_provenance.py` — bounds, ordering, page range, hash agreement, non-finite coordinates.
- [ ] `tests/contract/test_document_schemas.py` — every matrix row, the negative cases in Acceptance, an independent field-set oracle, JSON Schema content, OpenAPI shape, import boundary.

**Acceptance Criteria:**
- Given a validated document of any type, when serialised, then `schema_version`, `document_type` and every field with provenance are present and no value was altered.
- Given the registry, when enumerated, then it contains exactly the seven types, each with version, prompt, check set and table set, and no `packing_list` entry.
- Given the invoice's `json_schema()`, when inspected, then it names `invoice.number` as required, constrains `line_items` indexed paths, and describes `Field`/`Provenance`; it is not a bare generic map.
- Given table metadata claiming rows that were never emitted, when validated, then it is rejected; given a gap named only in `unresolved_rows`, then it is accepted, and any unnamed gap is rejected.
- Given a `source.document_hash` that is not the segment identity derived from file hash, page range and type, when validated, then it is rejected even if every field repeats it.
- Given a required path present with an empty or whitespace-only value, when validated, then it is rejected.
- Given the published OpenAPI, when a document is inspected, then `fields` and `extension` are maps of `Field` with `provenance`, and source and table metadata use the same models as the envelope.
- Given `import trade_extract.schemas.registry`, when imported outside tests, then no `db`, `services`, `api` or provider module is imported.

## Implementation Notes

**Shape.** `schemas/base.py` holds declarative `FieldSpec` / `FieldSet` / `TableSpec` / `TableColumn` dataclasses. `DocumentEnvelope` is a Pydantic base that consults the subclass's `field_set` and `table_specs`; each of the seven modules subclasses it with a `Literal` `document_type` and `Literal` `schema_version`. Direct instantiation of the base is blocked by a `ClassVar` sentinel. `Field` and `Provenance` live in `domain`, so the provider schema, validator and public API share one definition without provider, SQLAlchemy or FastAPI imports.

**Registry and the seven-type freeze.** `DOCUMENT_SCHEMAS` contains exactly the seven story types. `docs/06` §12/§14 also sketches `packing_list`; that illustration was left untouched, but the story's freeze block wins, so `packing_list` and `unknown` remain segmentation outcomes in `DocumentType` and are deliberately absent from the registry. An eighth type is a new model, field set and one `SchemaSpec`; `SchemaSpec.__post_init__` rejects a model whose declared type/version or table set disagrees with its entry, and `get_schema` raises `UnsupportedDocumentTypeError` for anything else. No pipeline branch was added.

**Values and provenance.** Field values are the page characters plus their declared `value_type`; no normalisation, coercion or arithmetic is performed. Every `Field` requires `Provenance` (`page_index`, `bbox`, `reader`, `document_hash`, `coordinate_space`, optional confidence). Bboxes must be ordered and inside 0–1, `page_index` must fall within `source.page_start..page_end`, and the provenance hash must equal the segment's `document_hash`. `FieldSpec` enforces required paths and exact declared types; a missing required path names the path and says it is required. Alternatives are accepted only for `disputed` agreement. `source` is required because its page range is what makes page validation possible; server-owned IDs remain optional.

**Proforma identity.** A proforma is not an invoice, so its field set is derived from the invoice field set with every `invoice.*` path renamed to `proforma.*` (including `note` and `tax_point_date`), `proforma.valid_until` added, and `bill_of_lading.number` removed. This is an interpretation of docs/06 §6 rather than a verbatim list; the version bump is `proforma.peppol-1.0`.

**Extensions.** Extension keys must match `extension.<lowercase ISO 3166-1 alpha-2>.*` or `extension.any.*`; `extension.kenya.pin` is rejected. Validation enforces the namespace *shape* only, not membership in a full ISO country list, so national additions stay data. Unknown primary paths such as `invoice.kenya_vat` are rejected because only declared field-set paths and the extension namespace are legal.

**Tables.** Row paths use `[]` templates (`line_items[].quantity`) and concrete indices must start at 0 with no gaps. A table is complete only when the emitted row count equals the source's declared count; `truncated: true` is always rejected. Any shortfall must be exactly enumerated in zero-based `unresolved_rows`. Declared tables require their metadata (`TableMeta`) whenever rows are emitted, and unknown table names are rejected.

**Custom entry.** `custom_entry.py` sets `PROVISIONAL = True` and documents `PROVISIONAL_REASON`; the registry mirrors this as `SchemaSpec.provisional`. The WCO-derived field set is explicitly a first pass to be corrected against real customs documents.

**API contract.** `api/schemas/results.py` types `fields` and `extension` as `dict[str, DocumentField] | None`, importing `domain.fields.Field` as `DocumentField`, so published OpenAPI exposes `Field` with required `provenance`. No checks, conflicts or review items were added to the envelope, per the story boundary.

**Tests.** `tests/unit/test_provenance.py` covers bounds, ordering, non-finite coordinates, page range, hash agreement and confidence. `tests/contract/test_document_schemas.py` covers seven-type round-trips, all eleven matrix rows, registry completeness and the seven-only rule, additive eighth-type acceptance, missing provenance, required row cells, array gaps, table metadata coverage, published OpenAPI `Field` maps, and the import boundary.

**Docs aligned after implementation.** `docs/06` extension paths were renamed from `extension.kenya.*` to `extension.ke.*` (16 occurrences) so the design document matches the "national context is data" rule the code enforces; §12 and §14 now record that v1 ships seven types and that a packing-list segment has no schema and routes to review. `docs/08` ADR-013 and open item A-01 record the product's deferred decision instead of the earlier "pending sign-off" proposal.

## Spec Change Log

- **Loopback 1 (bad_spec).** Three review layers found defects the first spec did not prevent: `json_schema()` returned a generic map with no per-path constraints, so it could not serve as the provider output contract; table metadata was never cross-checked against emitted rows, so two declared rows validated with zero row data; and "no gaps" contiguity contradicted `unresolved_rows`, leaving no legal way to emit a document with an unresolvable middle row. Amended Code Map, Tasks, Acceptance and Design Notes to require per-path JSON Schema generation, row-count cross-checks, source-row indexing with named gaps, hash derivation checks, non-empty required values, alternative/provenance agreement, string-only v1 extensions, pinned `Literal` type/version, table-to-field-set registration checks, shared API models and an independent test oracle. Avoided known-bad states: a provider prompt with no field list; complete-looking metadata hiding omitted rows; a contract that cannot represent partial extraction. **KEEP:** the module layout (`domain/provenance.py`, `domain/fields.py`, `schemas/base.py`, `schemas/common.py`, seven type modules, `registry.py`); declarative `FieldSpec`/`FieldSet`/`TableSpec` dataclasses; one `DocumentEnvelope` base with `ClassVar` field and table sets and `Literal` type/version; all seven field sets and their paths, requiredness and value types exactly as derived from `docs/06`, including the full proforma rename and the removal of `bill_of_lading.number`; the extension namespace rule and its error messages; the provenance validators and their actionable, path-naming errors; the registry's seven-type freeze and additive-eighth-type test; the test helper structure (`payload`, `leaf`, `provenance`, `source`, `table_meta`, `invoice_with_lines`) and the import-boundary subprocess test. The first derivation passed 253 tests, so re-derivation must keep every behaviour that was not found defective.

## Review Triage Log

| Finding | Verdict | Evidence | Route |
|---|---|---|---|
| `json_schema()` is a generic map with no per-path constraints | high | `SchemaSpec.json_schema()` returned `model_json_schema()`; `FieldSet`/`TableSpec` are `ClassVar`s, so no path, requiredness or table rule reaches the schema | bad_spec |
| Table metadata is never cross-checked against emitted rows | high | `validate_tables` reads only metadata, `validate_table_coverage` only the reverse; a two-row declaration with zero `line_items[]` fields validated | bad_spec |
| Array contiguity contradicts `unresolved_rows` | high | Rows `[0, 2]` with `unresolved_rows: [1]` are rejected as a gap, while the passing test declared an emitted row unresolved | bad_spec |
| `source.document_hash` is not verified as the derived segment identity | medium | `EnvelopeSource` checked hash syntax only; any 64-hex value repeated by every field passed | patch, carried into loopback |
| Required paths accept empty values | medium | `Field.value` is a plain `str` and required validation checked key presence only | patch, carried into loopback |
| Documented extension value types are unenforced | low | The extension loop checked only the namespace; `extension.ke.pin` with `integer` passed | patch, carried into loopback |
| `FieldAlternative` reader/confidence can contradict its provenance | medium | No consistency check between `alternative.reader` and `alternative.provenance.reader` | patch, carried into loopback |
| Registry does not require `Literal`-pinned type/version | low | `SchemaSpec` compared defaults only; a broadly annotated model could register and then emit another version | patch, carried into loopback |
| Registry does not cross-check tables against the field set | low | A table with no `<table>[]` paths, or columns absent beneath the row, registered cleanly | patch, carried into loopback |
| Public API keeps parallel weaker `Source` and untyped `tables` | medium | `api.schemas.results.Document` reused only `Field`; `TableMeta` and `EnvelopeSource` were not shared | patch, carried into loopback |
| Round-trip tests are self-referential | medium | `payload()` and its assertions both derive from the same `FieldSet`, so dropping or renaming a path stayed green | patch, carried into loopback |
| Proforma rename exceeds the documented list | low | Code renamed `invoice.note` and `invoice.tax_point_date`; `docs/06` §6 listed only five identity paths | patch, carried into loopback |
| `docs/06` §14 still shows an active `packing_list` registry entry | low | Prose said seven types while the literal still listed eight | patch, carried into loopback |
| Huge array indices can exhaust memory | low | `set(range(max(indices) + 1))` materialises the whole range before rejecting | patch, carried into loopback |
| Edge: `row_count_extracted` can disagree with emitted rows | high | Same defect as the table cross-check finding; grouped with it | bad_spec |
| Edge: alternative reader can differ from its provenance reader | medium | Same defect as the alternative-consistency finding; grouped with it | patch, carried into loopback |
| Gap: no negative test for field/source hash agreement at the envelope | medium | Fixtures always matched hashes; removing the `provenance.matches` branch failed no test | patch, carried into loopback |
| Gap: disputed-field and alternative-provenance rules are untested | medium | No test constructed an alternative or asserted the agreement rules | patch, carried into loopback |
| Gap: unresolved-row cardinality has no failing case | low | Only the empty-list case was covered; removing `len(rows) != missing` stayed green | patch, carried into loopback |
| Gap: JSON Schema assertions would accept `{"title": ...}` | high | Same root cause as the generic-`json_schema` finding; grouped with it | bad_spec |
| Gap: metadata can certify rows never emitted | high | Demonstrated directly by the reviewer; grouped with the table cross-check finding | bad_spec |

## Design Notes

The field set is data: one generic envelope validates seven types by consulting a declarative `FieldSet`. `Field` is emitted; `FieldSpec` decides what is legal.

**Row indices identify source rows, not dense output rows.** `line_items[2]` is the third row on the page. Emitted row indices must start at 0 and be contiguous *except* where `unresolved_rows` names the gap, and a row may not be both emitted and unresolved. Required row cells are required for every emitted row only. For a declared table, `row_count_extracted` must equal the number of distinct emitted row indices, emitted indices must fall inside the stated range, and emitted plus unresolved rows must partition it. Arrays with no declared table stay strictly contiguous from 0. Contiguity must be decided by comparing counts, never by materialising `range(max_index + 1)`.

**`json_schema()` is the provider contract, not a formality.** Generate it from the field set: scalar paths as named properties, indexed paths as `patternProperties`, required scalar paths in `required`, `Field`/`Provenance` as the value schema, declared tables with their metadata, and the extension namespace as a property-name pattern. A generic `additionalProperties: Field` map alone is a defect.

**The envelope proves itself.** `source.document_hash` must equal `document_hash(file_hash, page_start, page_end, document_type)`, every leaf and alternative must sit inside the source page range and repeat that hash, and a required path must carry a non-empty, non-whitespace-only value. Rejecting emptiness is not normalisation: the characters are still emitted untouched.

**v1 extension leaves are strings.** `docs/06` types every extension path as `string`; enforce it, and leave typed extensions to a future declared spec.

## Verification

**Commands:**
- `uv run pytest tests/unit/test_provenance.py tests/contract/test_document_schemas.py -q` — expected: all pass.
- `uv run pytest -q` — expected: all pass, including the integration suite.
- `uv run ruff check src tests` and `uv run mypy src` — expected: clean.
