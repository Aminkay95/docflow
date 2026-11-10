---
title: 'Local-first cost-capped document extraction'
type: 'refactor'
created: '2026-11-04'
status: 'in-progress'
baseline_commit: 'd7bd31aa72c312b0b4ec43983443212e210d97f1'
route: 'dispatch'
review_loop_iteration: 0
context:
  - 'docs/01-architecture.md'
  - 'docs/06-document-schemas.md'
  - 'docs/07-implementation-blueprint.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Live QA produced no usable structured documents while provider spend reached about USD 1.40 across the broader QA work. The current readers send page-level character geometry plus the broad document schema to the LLM, retry large invalid responses, and make successful extraction too slow and expensive to replace manual entry.

**Approach:** Make extraction local-first: derive high-confidence candidates deterministically from embedded text or PaddleOCR geometry, validate them locally, and invoke DeepSeek only for unresolved scalar groups or bounded table batches. Enforce per-page provider budgets, preserve partial valid work, and measure field accuracy, latency and cost on the existing public QA corpus.

**Approved targets:** Treat USD 0.04 per page as the measured corpus-average provider-cost target, with a higher per-page safety ceiling for difficult pages. Release requires at least 95% exact match on critical scalar fields, 90% table-cell accuracy, and p95 processing latency below 90 seconds per page on the labelled evaluation set.

## Boundaries & Constraints

**Always:** Preserve the existing database/API schemas, document schemas, field paths, source identity, normalized provenance, reader attribution, unreadable refusal and table-completeness gates. Prefer exact labels, regex, geometry and arithmetic over inference; local extraction may emit only stated values backed by verified text spans. Deduplicate evidence, use compact stage-specific provider contracts, cap input/output/calls and repair only the failed stage once. Record token, call, latency, truncation and budget diagnostics without document content. Fall back to review when evidence is ambiguous or a budget is exhausted.

**Never:** Invent fields, weaken provenance validation, represent OCR confidence as semantic certainty, silently omit required table rows, send full duplicated document evidence to every call, retry a whole document because one stage failed, train or add a heavyweight local model, add a GPU requirement, alter the schema, or promise zero manual review for unreadable/ambiguous documents.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Common digital invoice | Embedded text with trusted boxes and conventional labels | Local rules extract identifiers, parties, dates, currency and totals; provider receives only unresolved groups | Conflicts remain unresolved and route to review |
| Scan or photograph | Conditioned page with bounded Paddle spans | Same local rules operate on OCR spans with Paddle provenance | Low-confidence or unmatched evidence is not promoted |
| Irregular scalar layout | Some required scalar groups unresolved | One compact stage call receives only relevant lines/boxes and a field-subset schema | One targeted repair; retain successful local/stage fields |
| Long or irregular table | Table rows exceed a single bounded response | Extract locally where reliable, otherwise batch rows with stable indices and merge deterministically | Count/conflict/truncation mismatch routes table to review |
| Provider budget reached | Next call would cross the configured page budget | Stop provider escalation and return valid partial evidence for review | Named budget diagnostic; no overspend retry |
| Blank/unsupported document | No reliable stated values or supported schema | Explicit unsupported/unreadable/review outcome | No provider call when evidence cannot improve classification |

</frozen-after-approval>

## Code Map

- `src/trade_extract/services/extraction.py::extract` — currently builds one broad request, runs every eligible reader, and repairs whole reader documents; split into deterministic local candidates and bounded scalar/table stages while retaining merge and provenance guards.
- `src/trade_extract/services/readers/text_layer.py`, `readers/paddle.py` — expose verified line/span evidence once; avoid serializing every character box into repeated provider prompts.
- `src/trade_extract/services/readers/base.py::partial_model` and `schemas/registry.py` — derive compact field-subset contracts without changing registered document models.
- `src/trade_extract/providers/prompts`, `providers/deepseek.py`, `domain/inference.py` — add stage identity, limits, finish diagnostics and pre-call budget enforcement; retain content-free provider-call logging.
- `src/trade_extract/services/segmentation.py`, `workers/handlers/extract_segment.py` — reclassify from available OCR text before escalation and surface partial/review diagnostics through the existing lifecycle.
- `src/trade_extract/config.py`, `.env.example`, `docker-compose.yml` — define validated call/token/cost and stage-size limits for workers.
- `tests/unit/test_extraction.py`, `test_readers.py`, `test_providers.py`, `test_segmentation.py` — cover local-only extraction, targeted fallback, truncation, repairs, budget exhaustion and provenance.
- `docs/evaluation.md`, `docs/01-architecture.md`, `docs/07-implementation-blueprint.md` — document the local-first decision tree and reproducible cost/accuracy/latency gate.

## Tasks & Acceptance

**Execution:**
- [x] Add image-free local evidence indexing and deterministic trade-field extractors with exact provenance validation.
- [x] Add compact scalar-group and row-batched table contracts, deterministic merge, targeted repair and partial-success behavior.
- [x] Enforce provider call/input/output/cost budgets before dispatch and persist safe diagnostics through existing records.
- [x] Reclassify unknown/weak segments after OCR without guessing, and avoid provider calls for blank/unsupported evidence.
- [x] Add regression and evaluation coverage, then rerun the six public QA samples and publish comparable metrics.

**Acceptance Criteria:**
- Given a conventional document whose required fields are locally unambiguous, when extraction runs, then it emits schema-valid fields with exact provenance and makes no provider call.
- Given unresolved fields, when escalation runs, then each call contains one evidence copy and only its stage schema, and a failure does not discard valid stages.
- Given a provider response that truncates or violates a stage contract, when repair runs, then only that stage is repaired once and the document never enters an unbounded retry loop.
- Given the approved cost and quality policies, when the labelled evaluation corpus runs, then the measured spend, accuracy and p95 latency satisfy those gates or the release fails explicitly.

## Implementation Notes

Implemented a canonical image-free line index shared by embedded text and OCR evidence. Conservative invoice rules promote only uniquely matched stated values with exact character-derived provenance. Already resolved fields are removed from the provider contract; unresolved required scalars use a compact evidence-ID response, and existing bounded table batches remain independently merged. Provider responses are reconstructed into the unchanged document schema and revalidated locally. Invalid, truncated or over-budget stages retain verified partial fields for review instead of discarding them.

Added pre-dispatch evidence, input-token, output-token, call-count and estimated-cost limits; one targeted repair is permitted per failed stage. Finish reasons, usage, latency, truncation and budget diagnostics remain content-free. Unknown segments are reclassified from OCR text only when one type has strong deterministic evidence; blank, unsupported or ambiguous evidence stops before provider dispatch.

The six unlabelled public smoke samples were rerun after the compact-contract change. Across the most complete comparable run, provider usage was 35,226 input tokens, 37,795 output tokens and 4,736 cached input tokens over six calls, with 304.8 seconds total provider latency. Using the configured DeepSeek prices, estimated provider spend was approximately USD 0.095 total, or USD 0.0158/page average, below the USD 0.04/page cost target. Two of six samples produced usable partial review records; four failed, mainly because responses truncated. The observed worst-page latency was about 152 seconds, so the p95-under-90-seconds release gate is not met. Critical-field and table-cell accuracy remain unmeasured because the public samples do not have human-authored truth labels. This implementation therefore remains `in-progress` and is not release-ready.

A later experimental keyed-map response shape was rejected by the live provider with `http_400` before token use in two smoke attempts. The implementation retains the previously accepted bounded array contract and validates/deduplicates paths locally; the rejected format is not shipped.

## Spec Change Log

- 2026-11-05: Implemented local-first extraction, bounded provider escalation, OCR reclassification, diagnostics and regression coverage without database or API schema changes.
- 2026-11-05: Recorded live public-sample cost and latency. Cost target passed; coverage, latency and labelled-accuracy release gates remain open.

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Keyed compact response contract | rejected | DeepSeek returned `http_400` with zero tokens on two live attempts; retained the earlier provider-compatible bounded array and local path deduplication. |
| Public-smoke cost | pass for unlabelled smoke | Estimated USD 0.0158/page average, below the approved USD 0.04/page corpus-average target. |
| Public-smoke latency | fail | Observed worst page about 152 seconds; the approved p95 gate is below 90 seconds/page. |
| Extraction coverage | fail | Only two of six samples yielded usable partial review records; truncation remained the dominant failure mode. |
| Accuracy gate | unmeasured | Public samples have no human-authored scalar/table truth; user comparison is still required. |

## Design Notes

Local extraction should first create a canonical ordered evidence index of lines and spans. Rules return candidates, never final approval. Provider stages consume references into that index and return values plus evidence identifiers; application code reconstructs and validates canonical provenance, so the model does not need to reproduce large bounding-box objects.

## Verification

**Commands:**
- `uv run pytest tests/unit tests/contract -q` — 383 passed; the existing Starlette/httpx deprecation warning remains.
- `uv run pytest tests/integration -q` — 133 skipped because the configured test PostgreSQL service was unavailable on port 5432; the isolated QA stack uses port 5434.
- `uv run ruff check .` and `uv run mypy` — pass.
- Live DeepSeek public smoke — cost target passed; extraction coverage and latency failed; accuracy is unmeasured pending labelled truth.
