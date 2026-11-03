---
title: 'PaddleOCR reader, table structure and reader routing'
type: 'feature'
created: '2026-11-03'
status: 'done'
baseline_commit: 'eac7ed18f92461458e7e81e32150b684222b8997'
route: 'dispatch'
review_loop_iteration: 1
context:
  - 'docs/01-architecture.md'
  - 'docs/07-implementation-blueprint.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Extraction currently runs text and vision across a segment without per-page routing. There is no OCR reader or independent table inventory, so a provider can silently omit rows while reporting a self-consistent shorter table.

**Approach:** Add PaddleOCR text/table evidence behind the reader interface, deterministic versioned per-page routing, and native-resolution region reading with independent row coverage reconciliation. Return every detected row or identify its unresolved position; never present shorter output as complete.

## Boundaries & Constraints

**Always:** Follow story 6's baseline: a demonstrably good digital text layer uses text alone; clean flat scans use OCR plus vision and retain disagreement; photographs, skew, stamps and handwriting use vision as primary, with OCR only supporting structure/evidence. Dense tables require structural evidence and native-resolution crops even on digital pages. Record route reasons, effective policy and reader availability. Unknown features must not be treated as evidence of a clean scan. Route unreadable pages to no reader and preserve refusal reasons. Keep source hash, page and segment identity and map crop/OCR geometry through conditioning transforms into the established provenance convention. Validate actual OCR spans before accepting OCR-derived fields. Keep all provider calls behind the adapter and the bounded repair/schema-validation path. OCR confidence may be recorded, never used to approve output. Bound pixels, detected regions, crops, row counts and provider batches; budget exhaustion must produce an explicit incomplete/failure diagnostic.

**Always:** Build an independent ordered table-row inventory across pages; reconcile provider-emitted row indices against it, preserving exact unresolved rows and ambiguous structure diagnostics. Missing OCR, initialization failure or malformed structure must remain visible and prevent an unsupported completeness claim. Continue safe readers where evidence permits. Keep imports lazy, OCR worker-only and inference serialized per process. Preserve preparation retries, unconditional segmentation and API import isolation. Routing defaults remain provisional until story 7.

**Never:** Invent fields, boxes, rows or semantics from arithmetic; flatten detected table HTML into unverified complete output; resolve reader disagreement silently; read dense rows solely from whole-page downscales; let OCR become primary for stamps or handwriting. No lifecycle activation, invoice checks, approval gate, review UI, real-document uploads or live provider calls; lifecycle activation remains story 8. No database migration or external side effects are required.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Digital | Good aligned text, no dense regions | Text-only route | Weak text falls back conservatively |
| Flat scan | Conditioned clean scan | OCR and vision compared | OCR failure recorded; safe vision may continue |
| Photograph or marks | Photo/skew/stamp/handwriting evidence | Vision primary; OCR supports tables | Uncertain features cannot select clean-scan shortcut |
| Dense table | 200 rows, including page continuation | Ordered inventory plus native crops; all rows emitted or explicitly unresolved | Shorter self-reported counts cannot pass |
| Ambiguous structure | Merged cells, overlapping regions, missing structure | Preserve uncertainty and affected rows/regions | No invented row correspondence |
| Crop | Offset region on transformed page | Crop-local boxes map to correct page/source coordinates | Reject out-of-crop or mismatched geometry |
| Unsafe/unreadable | Refusal, malformed OCR, oversized image or exhausted budget | Named bounded outcome; no reader for refused page | No silent successful fallback |

</frozen-after-approval>

## Code Map

- `src/trade_extract/domain/readers.py`, `domain/inference.py` — extend image-free page features, OCR/table evidence, crop geometry and route diagnostics; reuse `ReaderKind.PADDLE`.
- `src/trade_extract/services/readers/base.py`, `text_layer.py`, `vision.py` — retain the existing provider-neutral Reader protocol and repair inputs; add lazy Paddle implementation and native crop handling.
- `src/trade_extract/services/extraction.py::extract` — replace unconditional default reader dispatch with per-page routes; fix vision-versus-characters evidence selection for Paddle, retain candidate disagreement and validate independent table coverage.
- `src/trade_extract/schemas/base.py::TableMeta` — already enforces emitted plus unresolved rows against stated count, but that count currently comes only from the provider; preserve these invariants.
- `src/trade_extract/domain/conditioning.py::CoordinateTransform`, `services/conditioning.py`, `workers/handlers/prepare_file.py` — reuse recorded operations/measurements and reversible geometry; preserve storage/idempotency behavior.
- `src/trade_extract/config.py`, `.env.example`, `pyproject.toml`, `uv.lock`, `Dockerfile.worker` — configurable policy/budgets and compatible pinned OCR runtime; API must not import/install OCR.
- `tests/unit/test_readers.py`, `test_extraction.py`, `tests/contract/test_api_imports.py` — existing reader/repair, completeness and isolation foundations.

## Tasks & Acceptance

**Execution:**
- [x] `src/trade_extract/domain/readers.py`, `domain/inference.py` — add immutable OCR, table, region, feature and routing-result contracts without heavy imports.
- [x] `src/trade_extract/services/readers/paddle.py` — implement lazy serialized PaddleOCR/PP-Structure adapter, validated text/cell geometry and explicit unavailable/error results; allow deterministic backend injection for tests.
- [x] `src/trade_extract/services/routing.py`, `config.py`, `.env.example` — implement versioned configurable routing with diagnostic reasons and conservative feature assessment.
- [x] `src/trade_extract/services/tables.py`, `services/readers/vision.py` — construct ordered row inventories, bounded native crops and coordinate mapping; reconcile multi-page coverage without trusting model counts.
- [x] `src/trade_extract/services/extraction.py`, `services/readers/__init__.py` — integrate routing/OCR/crops, repair-safe evidence validation and explicit incomplete/unavailable diagnostics while retaining reader disagreement.
- [x] `pyproject.toml`, `uv.lock`, `Dockerfile.worker` — pin compatible OCR dependencies and install only in worker; document model initialization/cache requirements.
- [x] `tests/unit/test_routing_tables_paddle.py`, `test_story6_boundary.py`, existing reader/extraction/contract tests — cover the matrix with synthetic images and injected OCR/provider results, independent coordinate expectations and a 200-row regression.
- [x] `docs/01-architecture.md`, `docs/07-implementation-blueprint.md` — document implemented routes, geometry, completeness, failure semantics and provisional defaults; align digital-text baseline with story 6.

**Acceptance Criteria:**
- Given mixed page features in one segment, when extraction dispatches, then each reader receives only its eligible pages and unreadable pages reach no reader, including repair attempts.
- Given a 200-row multi-page table, when a provider omits rows and claims a shorter complete count, then independent inventory detects the exact missing positions and no complete result is promoted.
- Given crops on a conditioned perspective fixture, when crop-local coordinates are mapped, then independently expected source landmarks align within two pixels.
- Given unavailable OCR or exceeded resource limits, when extraction runs, then outcome diagnostics expose the limitation and unsupported table completeness is rejected.
- Given changed routing thresholds, when equivalent evidence is processed, then route behavior and effective recorded policy reflect the change.
- Given API startup, when imports are inspected, then no PaddleOCR, Paddle, OpenCV, numpy, PDFium or provider modules are loaded.

## Implementation Notes

Implemented image-free immutable evidence, route, inventory and crop contracts; conservative configurable per-page routing; a lazy process-cached serialized CPU PPStructureV3 adapter; scalar OCR preservation on structural failures; bounded HTML/cell/geometry validation and cached-evidence identity checks. Worker-only dependencies are pinned to PaddleOCR 3.3.2 and Paddle 3.2.2, with required system libraries and writable caches. No lifecycle tasks, migrations, real uploads or live provider calls were added.

Extraction validates supplied scalar evidence with a private partial schema and validates required document fields after combining complementary readers/batches. Per-batch source, segment, reader and attached-row guards run before merge. Matching repeated scalar values retain a deterministic valid citation; differing values remain explicit errors within one vision reader, and cross-reader disagreements retain alternatives. Text-only calls do not initialize excluded OCR. Effective routing, OCR/crop budgets and provider image limits accompany outcomes.

Explicit column headers are excluded; row headers remain data. Bounded geometric overlap handles small cell alignment differences while per-row HTML cardinality and ambiguous/overlapping regions remain guarded. Semantic table correspondence uses validated row citations, followed by at most one native crop redispatch; anonymous or unrelated grids cannot silently join a semantic table. Independent coverage requires all rows emitted or explicitly unresolved. Native crops preserve actual source pixels and map through integer crop edges and the saved conditioning homography.

Matrix audit: digital/threshold and scan/marked/unknown routing; mixed readable/refused pages and repair eligibility; malformed/ambiguous/overlapping structures; unsafe decode and resource budgets; exact OCR spans at extraction; schema-valid short 200-row output; complementary reader pages; native 200-row batches; perspective landmarks within two pixels; attached crop pixels/dimensions; explicit unresolved rows; later-batch shape/identity failures; late OCR failure reasons and effective OCR settings all have executed regression tests. Existing preparation persistence/retry/lifecycle integration tests are retained.

Runtime verification distinguishes offline imports from model inference. The worker image builds and imports Paddle/PPStructureV3 with networking disabled. A synthetic cold-start smoke check downloaded model weights into the local story6-ocr-model-cache volume but reached its 180-second limit during setup; inference was not reached. Prewarm the documented model cache before enabling real extraction. Real-model accuracy and feature calibration remain story 7 work. Feature assessments are caller-owned positive evidence in this reusable service; unknown features route conservatively.

## Spec Change Log

- Review iteration 1: strengthen non-frozen implementation guidance for reader-local versus segment coverage, semantic header/region correspondence, bounded OCR parsing/cached evidence and per-batch validation. Avoid rejecting complementary reader evidence or trusting an unvalidated batch. KEEP image-free immutable contracts, lazy process-serialized OCR, explicit routing reasons, provisional configuration, independent row inventory, native crop geometry, API isolation and source/segment identity. Re-derive from the approved intent; do not weaken refusal or completeness guarantees.

## Review Implementation Guidance

- Reconcile each reader against only its eligible evidence, retaining global row identity; combine complementary page subsets before final independent coverage validation. Unknown/missing structure prevents completeness only for pages needing table evidence; explicitly non-table digital pages must not invalidate other pages. A digital page whose table requirement becomes evident needs structural verification, not silent provider-count trust.
- Preserve explicit HTML header semantics (thead/th), repeated continuation headers and region correspondence. Do not infer that every detected table/grid belongs to one semantic line-item table. Use explicit table_name correspondence where supplied; where correspondence cannot be established, retain ambiguity and refuse completeness rather than concatenate unrelated grids. Do not guess unmarked header rows.
- Validate every batch object, fields/extension/table mapping, document type/schema/segment and all untrusted identity attributes before merging. Malformed JSON/shape must enter bounded repair; conflicting identity must fail. Each request carries only metadata for its attached images with explicit image position, crop rectangle and row identity. Restrict returned citations to attached page/row evidence.
- Enforce cached OCR page identity, status/version, geometry and all span/region/row/cell/HTML budgets before use; validate every polygon vertex for finiteness before extrema. Preserve validated scalar OCR spans when table parsing fails, with separate explicit structural diagnostics. Do not initialize OCR for an explicit reader list that excludes it. Decode each source image once per crop operation, with pixel bounds before allocation; cap HTML/cells before parsing/materialization.
- Add extraction-boundary regressions for accepted and rejected Paddle citations, schema-valid shorter tables, complementary mixed-reader table pages, non-table digital companion pages, malformed/identity-changing batch responses, and refused-page reasons on table failures. Run full matrix tests and all required checks after corrections.

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: digital table shortcut | medium | Good digital route skips structural verification even if returned table metadata reveals table evidence is required. bad_spec; clarify conditional structural verification. |
| Blind 2: whole-segment reconciliation per reader | high | extract reconciles each eligible subset against global inventory before merging. bad_spec; preserve global indices and combine supported evidence. |
| Blind 3: headers counted as data | high | _Rows treats th/thead rows as data. bad_spec; preserve explicit header semantics. |
| Blind 4: semantic grids concatenated | high | build_inventory ignores table_name and combines unrelated physical regions. bad_spec; require correspondence or explicit ambiguity. |
| Blind 5: later batch identity discarded | high | Only source/tables checked; segment/type and forbidden identity in later payloads escape validation. bad_spec; validate every batch. |
| Blind 6: crop batch association | high | Global metadata lacks attached image positions. bad_spec; per-batch image metadata. |
| Blind 7: table failure loses scalar OCR | medium | inspect discards validated spans on table parse failure. bad_spec; retain scalar evidence with structural failure. |
| Blind 8: HTML/cell analysis unbounded | medium | Rows checked only after HTML/cell materialization. bad_spec; bound before analysis. |
| Blind 9: repeated source decoding | medium | native_crops decodes a page per row. bad_spec; decode once under existing pixel budgets. |
| Blind 10: explicit readers initialize OCR | medium | Inspection happens even when explicit readers exclude Paddle. bad_spec; honor configured availability. |
| Blind 11: cached OCR bypasses checks | high | Supplied p.ocr bypasses inspect and page/budget validation. bad_spec; validate all evidence before use. |
| Edge 1: headers | high | Same demonstrated header-index defect as Blind 3. bad_spec, grouped. |
| Edge 2: disjoint readers | high | Same global-versus-local reconciliation defect as Blind 2. bad_spec, grouped. |
| Edge 3: non-table digital companion | medium | build_inventory adds unavailable diagnostic for intentionally uninspected page, blocking supported table elsewhere. bad_spec. |
| Edge 4: malformed batch shapes | high | list payload/nonmapping sections raise AttributeError outside repair. bad_spec; validate objects first. |
| Edge 5: NaN polygon vertices | medium | extrema can discard nonfinite inner vertices. bad_spec; validate every coordinate before bounds. |
| Edge 6: refusal reasons lost | medium | coverage early return omits unreadable_errors. bad_spec; preserve reasons on every exit. |
| Verification 1: Paddle guard integration | medium | Existing helper tests do not execute extract's OCR provenance guard. bad_spec; add boundary tests. |
| Verification 2: independent table boundary | medium | Helper-only reconciliation test cannot detect removal of extract guard. bad_spec; add schema-valid short-table extraction test. |
| Review 2 blind 1: full batch scalar requirements | high | Demonstrated split scalar evidence across pages failed before merge. Patch: private partial scalar validator and final required-field validation; split-page regression passes. |
| Review 2 blind 2: equal repeated scalars | medium | Whole serialized equality rejected equal values with different validated citations. Patch: select a deterministic valid citation for equal stated evidence; repeated-header regression passes. |
| Review 2 blind 3: late named table | medium | Newly inspected named table did not enter native redispatch. Patch: include late named inventories in the existing bounded second pass; boundary regression passes. |
| Review 2 blind 4: unused native crops | medium | All inventory rows were cropped without eligible vision. Patch: crop eligible vision pages only; dense tables without required native reader fail explicitly. |
| Review 2 blind 5: row headers | medium | th scope=row incorrectly removed data rows. Patch: distinguish row headers from column headers; explicit regression passes. |
| Review 2 blind 6: exact cell alignment | medium | Exact y-coordinate grouping unnecessarily refused slightly misaligned cells. Patch: bounded vertical overlap with ambiguity checks; independent varied-cell regression passes. |
| Review 2 blind 7: row cell cardinality | high | Total-only HTML count could accept incorrect per-row correspondence. Patch: compare each geometric row's cell count with HTML; mismatched-row regression passes. |
| Review 2 blind 8: V3 normalization loses spans | medium | Missing cell geometry could fail normalization before scalar parsing. Patch: localize malformed table normalization; actual-shaped V3 scalar-preservation regression passes. |
| Review 2 blind 9: late inspection diagnostics | medium | Late inspection did not append underlying failure reasons. Patch: retain diagnostics on every inspection; failure-privacy boundary regression passes. |
| Review 2 blind 10: effective reader settings | medium | Explicit PaddleReader could inspect using different budgets. Patch: use extraction settings for inspection and reasoning; explicit-reader budget regression passes. |
| Review 2 edge 1: split scalar pages | high | Same demonstrated partial-batch validation defect as review 2 blind 1. Grouped patch and regression. |
| Review 2 edge 2: repeated citation pages | medium | Same equal-value/different-citation defect as review 2 blind 2. Grouped patch and regression. |
| Review 2 verification 1: actual crop pixels | medium | White fixtures and metadata-only assertions could miss blank crops. Patch: decode provider-attached colored-landmark crop and assert native dimensions/pixels with JPEG tolerance. |

All review findings were addressed; nothing was deferred.

## Verification

- `uv run pytest tests/unit tests/contract` — deterministic tests pass without model downloads or network calls.
- `uv run pytest tests/integration` — existing persistence/retry/lifecycle regressions pass when PostgreSQL is available; report infrastructure limitations explicitly.
- `uv run ruff check .` and `uv run mypy` — pass.
- Build the worker image and verify OCR imports/model setup if Docker and network infrastructure are available; distinguish this from injected-backend tests.

Final verification: 324 unit/contract tests and 101 PostgreSQL integration tests passed; Ruff, mypy (98 source files), uv lock --check and git diff --check passed. Worker image build and network-disabled Paddle 3.2.2 / PPStructureV3 imports passed. Cold model setup timed out after 180 seconds while downloading weights; actual model inference and accuracy remain unverified. All review findings were corrected, with no deferred findings. Existing Starlette/httpx deprecation warning remains.
