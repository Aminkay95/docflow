---
title: 'Lightweight staged OCR and lazy table recognition'
type: 'refactor'
created: '2026-11-04'
status: 'done'
baseline_commit: '3bf6df5bc39455698a3148f2f4d03ece8ad48dd2'
route: 'dispatch'
review_loop_iteration: 0
context:
  - 'docs/01-architecture.md'
  - 'docs/07-implementation-blueprint.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The worker constructs the full PPStructureV3 pipeline for every routed OCR page. Its general layout and table models exceed the local Docker memory budget, add cold-start latency, and prevent real OCR from participating in extraction.

**Approach:** Replace the monolithic reader with staged Paddle pipelines: use mobile text detection/recognition for ordinary scanned pages, skip OCR when trusted embedded PDF text is sufficient, and lazily run table structure only for pages explicitly requiring it while reusing base OCR results.

## Boundaries & Constraints

**Always:** Preserve `OCREvidence`, page identity, normalized coordinates, Paddle provenance, diagnostics, budgets and conservative table-completeness gates. Keep all Paddle, numpy and OpenCV imports lazy and worker-only. Serialize model initialization/inference, cache each stage per process, reuse base OCR for the table stage, and record effective model/staging settings in routing policy. Treat persisted text as sufficient only when actual character geometry meets the configured threshold and no table requirement is known; late table discovery must still invoke structure once. Keep existing database and API schemas.

**Never:** Load PPStructureV3, run table/layout models on every page, invent negative page features, trust OCR as proof of field correctness, silently downgrade a required table, or weaken unreadable/refusal and native-crop completeness behavior. Do not add GPU requirements, migrations, provider changes, or parallel Paddle inference.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Digital PDF | Sufficient embedded characters with boxes; no table required | Text layer only; no Paddle engine initialization | Fall back conservatively when evidence is insufficient |
| Ordinary scan | Conditioned image; no table requirement | Mobile OCR produces bounded spans; table engine remains unloaded | Retain safe OCR diagnostics and vision fallback |
| Dense or late table | Table required initially or discovered after first provider pass | Load table pipeline once, disable layout/duplicate OCR, reuse overall OCR evidence, preserve rows/cells | Required structure failure prevents promotion |
| Oversized/malformed page | Pixel, span, region, row, cell or HTML budget exceeded | Named bounded failure before unsafe work or persistence | No partial table represented as complete |
| Retry/mixed pages | Repeated extraction or text/scan/table mix | Deterministic per-page routes and process-cached engines | No duplicate base OCR within one extraction |

</frozen-after-approval>

## Code Map

- `src/trade_extract/services/readers/paddle.py::PaddleReader.inspect`, `normalize_v3` — split global PPStructure into lazy mobile `PaddleOCR` and `TableRecognitionPipelineV2`; add bounded normalizers while preserving evidence validation.
- `src/trade_extract/services/extraction.py::extract` — pass initial/late structure requirements, reuse cached base evidence, and avoid duplicate stage execution.
- `src/trade_extract/services/routing.py::route_page`, `workers/handlers/extract_segment.py::rehydrate` — conservatively identify sufficient embedded text without fabricating photo/skew/table negatives; retain unknown-page fallbacks.
- `src/trade_extract/config.py`, `.env.example`, `docker-compose.yml` — expose validated model names and OCR input bounds to the worker and policy metadata.
- `tests/unit/test_routing_tables_paddle.py`, `test_story6_boundary.py`, `tests/contract/test_api_imports.py` — cover lazy construction, cached OCR reuse, late tables, bounded failures, digital skips and import isolation.
- `docs/01-architecture.md`, `docs/07-implementation-blueprint.md`, `docs/08-operations-and-decisions.md` — document actual staged runtime, memory boundary and operating settings.

## Tasks & Acceptance

**Execution:**
- [x] Implement image-free stage contracts and effective settings without changing stored/API schemas.
- [x] Replace PPStructureV3 with mobile base OCR and lazy table recognition that consumes cached OCR output with layout and duplicate OCR disabled.
- [x] Integrate initial and late structure requirements, including deterministic digital-text bypass and one execution per stage/page.
- [x] Add unit/contract coverage for every matrix row and preserve existing story-6 boundary tests.
- [x] Update operator and architecture documentation with measured runtime behavior and configuration.

**Acceptance Criteria:**
- Given a usable digital PDF, when extraction runs, then neither Paddle engine initializes and text-layer provenance remains valid.
- Given a conditioned scan without a required table, when extraction runs, then only mobile OCR initializes and bounded spans can participate in extraction.
- Given an initial or late table requirement, when structure runs, then the table engine initializes once, consumes cached OCR, and incomplete structure cannot promote the document.
- Given API startup, when imports are inspected, then Paddle, numpy, OpenCV, PDFium and provider modules remain unloaded.
- Given the local QA worker, when representative samples run, then OCR no longer exits with code 137 under the existing Docker memory allocation, or the measured remaining blocker is reported without weakening defaults.

## Implementation Notes

Implemented process-cached mobile PaddleOCR and a separately lazy table-v2 stage. The reader retains Paddle's native OCR result by exact page identity for table hand-off while exposing only validated `OCREvidence`; externally supplied normalized evidence is never fabricated back into a runtime result and triggers a safe base rerun when structure is required. Digital pages with sufficient boxed characters bypass image readers, and late table redispatch preserves the same reader cache.

Pinned-runtime verification found and corrected two mock-hidden issues: the table wrapper requires separate wired/wireless constructor names, and its `overall_ocr_res` input must be Paddle's native result rather than a reconstructed dictionary. The wrapper also eagerly constructs internal OCR models despite disabling duplicate inference, so those slots are explicitly assigned the mobile models.

A provider-free Docker run on the public DHL invoice under a 3.7 GiB memory limit completed without exit 137. Base OCR produced 100 spans at 1,236,000 KiB peak RSS; the lazy table stage stayed under 1,485,944 KiB and conservatively reported `ambiguous_merged_cells` rather than claiming incomplete structure. Cached-model process startup measured 68.833 seconds for base initialization/inference and 40.701 seconds for first table initialization/inference; engines remain process-cached for subsequent pages.

Review patches preserve validated base spans when table prediction or normalization fails, bound predictor iteration and per-result table lists before materialization, delay raw-result caching until normalization succeeds, and require actual boxed-character counts for digital bypass. Native non-empty geometry, failure retention, overflow and distinct model/Compose mappings now have direct regression coverage. A final real-model run confirmed 100 base spans survive the merged-cell structure refusal at about 1.52 GiB peak RSS.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: table failure discards valid base spans | high | Verified: `spans` is populated only after table prediction/normalization, so the outer exception returns empty evidence. Patch locally so base normalization survives and only structure fails. |
| Blind 2: unbounded table result materialization | medium | Verified: `list(_TABLE_ENGINE.predict(...))` runs before the region budget. Patch bounded consumption with one-item overflow detection. |
| Blind 3: global engines ignore later model settings | false | Production settings are environment-only and immutable for a worker process; dynamic model switching within one process is not a supported caller path. |
| Blind 4: `good_text` can bypass boxed-character threshold | medium | Verified: the `or` branch permits a caller flag with insufficient actual character geometry, contrary to the frozen invariant. Patch routing to require both geometry counts. |
| Blind 5: arbitrary model identifiers are insufficiently validated | false | Model identifiers are intentionally operator-configurable and Paddle is the authoritative compatibility validator; restricting them to current defaults would remove supported model selection. |
| Blind 6: docs claim model-object reuse | false | The text says the wrapper's internal slots are assigned the mobile models, which accurately describes passing the configured model names; it does not claim `_OCR_ENGINE` object reuse. |
| Blind 7: table failures retaining base spans untested | medium | Verified with Blind 1; add failure and overflow regression coverage with the patch. |
| Blind 8: mojibake in spec | false | The file is UTF-8 and repository reads show the actual em dash; the corruption is in the review transport rendering. |
| Blind 9: smoke evidence is not independently reproducible | low | The ignored QA harness and public manifest retain the exact sample/path, while host-specific timing is diagnostic rather than a product contract; expanding committed surface is disproportionate. |
| Verification 1: staged non-empty spans/tables untested | medium | Pre-verified gap: native staged tests use empty payloads while non-empty tests exercise only the legacy backend. Add native-shaped non-empty evidence assertions. |
| Verification 2: custom model propagation untested | medium | Pre-verified gap: defaults cannot reveal hard-coding or wired/wireless swaps. Add distinct settings and rendered-Compose assertions. |
| Edge 1: unbounded predictor stream | medium | Same verified defect as Blind 2; bounded iteration must reject an extra result. |
| Edge 2: oversized per-result table list copied before rejection | medium | Verified: `tables.extend(current)` precedes the aggregate check. Check remaining capacity before extending. |
| Edge 3: malformed OCR result poisons raw cache | medium | Verified: the raw result is cached before `normalize_ocr`; move cache insertion after successful normalization. |
| Edge 4: `good_text` with too few boxes bypasses OCR | medium | Same verified defect as Blind 4; require the actual boxed-character threshold. |
| Edge 5: whitespace-only model setting | low | Verified configuration hygiene issue, but Paddle fails loudly at initialization and a dedicated validator adds negligible user value relative to normal environment validation; rejected. |
| Edge 6: routing metadata can differ from global engines | false | Same unsupported dynamic-settings scenario as Blind 3; each production worker has one immutable effective configuration. |

## Design Notes

`PaddleOCR` uses `PP-OCRv5_mobile_det` and `PP-OCRv5_mobile_rec`. `TableRecognitionPipelineV2.predict` receives `overall_ocr_res` from the base stage with `use_layout_detection=False` and `use_ocr_model=False`; table pages may use the full conditioned page as the bounded table input until independent table-region evidence exists.

## Verification

**Commands:**
- `uv run pytest tests/unit tests/contract -q` — all unit and import/Compose contracts pass.
- `uv run ruff check .` and `uv run mypy` — static checks pass.
- Rebuild the QA worker and run a representative public sample with normal OCR limits — no worker OOM; report stage status, memory and timing.

**Results:** 378 unit/contract tests passed with one existing Starlette/httpx deprecation warning; Ruff and mypy passed. The rebuilt provider-free worker completed real base and table inference on a public sample under the existing memory limit with the measurements recorded above.
