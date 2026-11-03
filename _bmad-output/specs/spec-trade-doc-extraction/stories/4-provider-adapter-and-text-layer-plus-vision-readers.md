---
title: 'Provider adapter and text-layer plus vision readers'
type: 'feature'
created: '2026-11-03'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'a5ccd2a68adf8a313aafc2bd53049fb3ae84c0c6'
context:
  - 'docs/01-architecture.md'
  - 'docs/07-implementation-blueprint.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Segments and validated schemas exist, but there is no model adapter or reader implementation. Digital-page text and image evidence cannot yet produce a validated field set or expose disagreements.

**Approach:** Add provider-neutral requests/results, DeepSeek and deterministic mock adapters, and embedded-text and vision readers behind one interface. A reusable extraction service validates responses, performs at most two repairs, and merges candidates without concealing disagreements.

## Boundaries & Constraints

**Always:** Use `deepseek-flash` for vision; Pro handles extracted text only. Keep provider wire formats inside `providers/`. Preserve stated strings, page coordinates and segment identity. Use the existing seven-type schema registry for validation, including provenance and table completeness. Record content-free metadata for every provider attempt. Keep dependencies injected and provider calls outside database transactions. Default to mock; live calls require explicit configuration.

**Never:** Invent boxes for embedded text, emit fields without provenance, use confidence to approve results, or silently resolve disagreement. No PaddleOCR, conditioning, automatic routing, invoice checks, conflict resolution, review UI or live document uploads in this story. Worker activation and persisted invoice extraction remain story 8; this story exposes and verifies the reusable service independently.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Digital page | PDF text geometry and rendered image | Both readers yield candidates with page boxes | Missing text yields no text candidates |
| Agreement | Two distinct readers state the same value | `unanimous`; original value retained | A lone reader is `single_reader` |
| Disagreement | Readers state different values | `disputed`, selected candidate plus alternatives retained | Selection follows explicit merge policy |
| Invalid response | Malformed JSON, schema or provenance | Retry repair with original evidence and validation errors | Initial call plus at most two repairs |
| Exhausted repair | Third response remains invalid | Explicit `extraction_failed`, no emitted document | Sanitized errors suitable for later review integration |
| Provider failure | Timeout, 429, 5xx or connection failure | Bounded retry with jitter and attempt metadata | 400 is not retried; no response body in errors |
| Unsafe image request | Pro with images, unsupported or excessive image payload | Reject before HTTP | Named capability/limit error |

</frozen-after-approval>

## Code Map

- `domain/fields.py`, `domain/provenance.py` under `src/trade_extract/` define reusable `Field`, `Agreement`, `FieldAlternative`, `BBox` and `Provenance`.
- `schemas/registry.py::get_schema` supplies concrete validators and provider JSON schemas; `schemas/base.py` validates source hashes, page bounds and table coverage. Preserve these contracts.
- `services/page_model.py` lazily imports PDFium and reads embedded text; extend worker-side geometry extraction without importing the image stack into API startup.
- `workers/handlers/prepare_file.py` currently creates queued documents then finalizes; leave lifecycle wiring unchanged until invoice extraction.
- `docs/07-implementation-blueprint.md` sections 7–10 define adapters, reader contracts, merge and repair; `docs/03-database-schema.md` defines the call ledger.
- Existing migrations are `0001_initial.py` and `0002_pages.py`; add an additive ledger migration. Existing `httpx` dependency supports injectable transports.

## Tasks & Acceptance

**Execution:**
- [x] `src/trade_extract/domain/{inference,readers}.py` — define provider-neutral immutable requests, results, errors, candidates and explicit failure outcomes.
- [x] `src/trade_extract/providers/{base,deepseek,mock,__init__}.py` and `providers/prompts/` — implement adapter protocol, factory, stable prompts, JSON mode, guarded image payloads, timeout/retry policy and deterministic scripted failures.
- [x] `src/trade_extract/config.py`, `.env.example` — add validated provider settings, masked API key, timeouts and bounded retry/repair settings; reject Pro as vision model.
- [x] `src/trade_extract/services/readers/{base,text_layer,vision,__init__}.py`, `services/page_model.py` — provide common reader interface, actual PDF text boxes and vision evidence; text-reader model calls use extracted text only and verify cited characters/boxes against source geometry.
- [x] `src/trade_extract/services/extraction.py` — implement schema validation/repair and candidate merge with injectable selection policy; pin source identity to trusted inputs, preserve alternatives and all candidates, and skip unreadable pages.
- [x] `src/trade_extract/db/models/provider_call.py`, `db/models/__init__.py`, `db/repositories/provider_calls.py`, `migrations/versions/0003_provider_calls.py` — persist content-free attempt metadata through an injected recorder using short independent transactions.
- [x] `tests/unit/test_{providers,readers,extraction}.py`, `tests/integration/test_provider_calls.py`, `tests/contract/test_api_imports.py` — cover matrix, real PDF geometry, bounded repair, retry metadata, source tampering, prompt stability and import boundaries using synthetic fixtures and mocked HTTP.

**Acceptance Criteria:**
- Given a synthetic digital invoice and scripted provider, when the extraction service runs both readers, then the resulting field set validates against the invoice schema and every leaf has a source-backed box.
- Given reader inputs in a different order, when the same merge policy is applied, then agreement and selection remain stable; repeated candidates from one reader never count as independent agreement.
- Given repair exhaustion, when the result is returned, then no invalid partial document is promoted and the outcome names the failure.
- Given recorded calls, when ledger and logs are inspected, then only IDs, model, prompt hash, token counts, timings, status and error codes are present; no document text, images, fields, credentials or provider response bodies appear.
- Given the API import test, when startup imports run, then no providers, OCR or PDFium are loaded.

## Implementation Notes
Implemented reusable provider-neutral contracts, DeepSeek and scripted mock adapters, exact PDF character geometry, dual readers, bounded validation/repair and deterministic merge. Live inference requires explicit configuration and an attempt recorder; no live calls were made. The additive provider ledger commits independently of extraction transactions. Worker activation remains with story 8.

Review corrections preserve diagnostic paths without values, pin citations to reader-specific evidence, reject truncated value spans, clear provider lifecycle metadata, record protocol/usage failures, preserve exact decimal comparison and stabilize full-field selection. Rotated PDF geometry was independently verified at 90, 180 and 270 degrees. Every surviving review finding was patched; nothing was deferred.

## Spec Change Log

## Review Triage Log
| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: repair diagnostics | medium | Generic errors discard both field location and known guard codes, preventing targeted repair. Patch existing error strings. |
| Blind 2: envelope metadata | medium | Valid statuses such as resolved and provider timestamps survive model_dump; approved itself is rejected by enum. Patch service-owned metadata. |
| Blind 3: image/page identity | high | Attached image page index is unchecked before its index is sent to provider. Patch consistency guard. |
| Blind 4: unsent vision page | high | Global page_map accepts citations to readable pages without images. Patch reader-specific evidence check. |
| Blind 5: comparison normalization | false | Architecture blueprint section 9 explicitly authorizes comparison-only decimal/whitespace normalization; emitted strings remain unchanged. Precision loss is a separate verified finding below. |
| Blind 6: substring citation | high | Value 10 can pass a source citation containing only 100. Patch verification of the emitted value span. |
| Blind 7: protocol failure | medium | RemoteProtocolError is not NetworkError and escapes without an attempt. Patch transport exception coverage. |
| Blind 8: no-op ledger | medium | Live constructor/factory default recorder discards all attempt evidence. Patch to require explicit live recorder. |
| Blind 9: reader instructions | medium | Schema allows reader enums but prompt never specifies executing reader. Patch evidence instructions with expected identity. |
| Edge 1: protocol failure | medium | Same demonstrated uncaught RemoteProtocolError as Blind 7. Grouped patch. |
| Edge 2: unsent vision page | high | Same missing image-evidence check as Blind 4. Grouped patch. |
| Edge 3: image/page identity | high | Same attached index mismatch as Blind 3. Grouped patch. |
| Edge 4: decimal rounding | high | Decimal.normalize rounds distinct 30-digit values identically under default precision (reproduced). Patch comparison to preserve exact Decimal equality. |
| Edge 5: selection tie | medium | Selection key omits raw_text/model; min retains input order for equal keys. Patch canonical full-field tie-break. |
| Edge 6: token overflow | medium | int(float('inf')) raises uncaught OverflowError. Patch malformed-usage exception handling. |
| Verification 1: rotated geometry | medium | Existing tests cover only zero rotation and derive boxes from implementation output. Patch independent 90/180/270 degree expected-coordinate tests. |

## Verification

- `uv run pytest tests/unit tests/contract` — existing and new checks pass without network access.
- `uv run pytest tests/integration` — PostgreSQL-backed migration/ledger and existing lifecycle checks pass when the test database is available; report unavailable infrastructure explicitly.
- `uv run ruff check .` and `uv run mypy` — pass.

Final verification: 241 unit/contract tests and 100 PostgreSQL integration tests passed; Ruff and mypy passed. A final adapter-model provenance correction passed all 11 extraction tests plus Ruff/mypy. Existing Starlette/httpx deprecation warning remains.
