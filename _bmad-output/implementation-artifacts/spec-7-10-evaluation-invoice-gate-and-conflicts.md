---
title: 'Stories 7–10: evaluation, invoice extraction, checks and conflicts'
type: 'feature'
created: '2026-11-03'
status: 'done'
baseline_commit: '7b9975c38efa0f3e08dcb4e976303abc1ddf310e'
route: 'dispatch'
review_loop_iteration: 0
context:
  - 'docs/01-architecture.md'
  - 'docs/03-database-schema.md'
  - 'docs/07-implementation-blueprint.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Stories 1–6 prepare documents and expose reusable readers, but workers do not extract invoices, evaluate accuracy, apply invoice checks or report cross-document conflicts.

**Approach:** Implement stories 7, 8, 9 and 10 in dependency order. Build complete-job corpus intake and evaluation first, then activate invoice extraction, deterministic checks and a versioned review gate, followed by canonical cross-document comparison. Keep the four deliverables separately testable within the requested combined scope.

**Approved corpus decision:** Implement and verify with synthetic complete-job fixtures. Real-corpus evaluation and calibration remain pending until a labelled real corpus is supplied; never represent synthetic results as real accuracy. The user approved all four stories together on 2026-11-03.

## Boundaries & Constraints

**Always:** Preserve stated field values, mandatory portable provenance, original file identity, source coordinate mapping, unconditional segmentation and preparation retry guarantees. Keep heavy libraries and providers out of API imports. Bound repair, OCR and crop work. Use Decimal for arithmetic. Record effective policy and check versions with every decision. Separate real and synthetic evaluation reports; publish sample sizes and refuse rankings below twenty documents per type. Full review remains the default until product-owned policy permits otherwise. Comparisons never alter document values or pick a winning document. Preserve tenant isolation and idempotent task results.

**Never:** Fabricate corpus labels or accuracy numbers, tune downstream thresholds using synthetic scores, compute duty/levies/CIF, use model confidence to gate, upload real documents or make live provider calls without explicit authorization. No review console, trust promotion, repeat-call accumulation feature, activation of the remaining document types, deployment or production data mutation.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Corpus | Complete labelled jobs with explicit real/synthetic origin | Per-type/per-field metrics, provenance hit rate, row completeness, review/pass/unreadable rates and conflict precision/recall; JSON and Markdown artifacts | Reject malformed labels and unsafe paths; report insufficient samples and absent real corpus |
| Invoice | Prepared invoice segment with conditioned evidence | Validated schema with stated fields and source provenance, persisted once and exposed through polling/webhook | Unreadable pages reach no reader; bounded failure retains diagnostics |
| Invoice checks | Stated amounts, identifiers and dates | INV-001 through INV-014 with expected/actual and human-readable reasons; missing optional inputs skipped | Invalid or ambiguous operands cannot silently pass; computed amounts stay exclusively in check results |
| Policy | Versioned gate configuration | Recorded policy controls arithmetic tolerance and review depth without deployment | Invalid configuration fails closed; confidence never changes pass/review |
| Conflict | Multiple documents in one asserted group | One canonical conflict names every source and preserves each value; job routes to conflict review | Missing or incomparable units are explicit; no inferred grouping |
| Retry/concurrency | Repeated extraction or finalization | One consistent persisted result and conflict set; completion waits for extraction | Atomic writes and existing queue retry/dead-task behavior |

</frozen-after-approval>

## Code Map

- `services/extraction.py::extract` already merges readers, validates schemas/provenance, bounds repair and reconciles tables; reuse rather than creating another inference path.
- `domain/readers.py`, `domain/conditioning.py`, `services/routing.py` define trusted evidence and refusal contracts. Rehydrate persisted page transforms and characters for worker extraction.
- `schemas/invoice.py::INVOICE_FIELD_SET` defines invoice fields and required cells; `domain/fields.py` preserves stated strings and reader disagreement.
- `workers/handlers/prepare_file.py` creates documents; `finalize_submission.py` currently completes after preparation; `workers/runner.py` has no extraction handler.
- `db/models/document.py` has extraction JSONB and status/decision columns; `db/models/policy.py` stores versioned JSON policy. Reuse these alongside dedicated check/conflict persistence prescribed in `docs/03-database-schema.md`.
- `services/ingest.py::build_result` currently returns null extraction/check/decision data and empty conflicts. Polling and webhook payloads share this result builder. Correct progress counting so queued/processing document rows are not counted as completed.
- `docs/07-implementation-blueprint.md` sections 11–13 and 22 specify checks, conflict fingerprints, decisions and evaluation. No eval runner or corpus currently exists.

## Tasks & Acceptance

**Execution:**
- [x] `src/trade_extract/services/evaluation.py`, `scripts/eval.py`, `tests/unit/test_evaluation.py`, `docs/evaluation.md` — implement validated complete-job labels, injected pipeline execution, stratified metrics, confidence intervals and reports; document corpus intake and scratch execution.
- [x] `workers/handlers/extract_segment.py`, `workers/handlers/prepare_file.py`, `workers/handlers/finalize_submission.py`, `workers/runner.py`, `services/queue.py` — dispatch idempotent invoice extraction outside long DB transactions; wait before completion; terminalize exhausted failures.
- [x] `services/checks.py`, `services/decisions.py`, `domain/checks.py`, `db/models/policy.py` — implement documented invoice checks and validated versioned policy; default to full review; preserve effective policy snapshots.
- [x] `services/conflicts.py`, `domain/canonical.py`, `db/models/`, `db/repositories/`, `migrations/versions/` — persist checks and unresolved conflict sources with stable identities, tenant/job scoping and retry safety.
- [x] `services/ingest.py`, `api/schemas/results.py`, `docs/openapi.yaml` — expose actual extraction, checks, decisions, conflict sources and separate review counts through existing includes and webhook snapshots.
- [x] `tests/unit/`, `tests/integration/`, `tests/contract/`, `docs/01-architecture.md`, `docs/07-implementation-blueprint.md` — verify matrix and document implemented limits and unmeasured corpus status.

**Acceptance Criteria:**
- Given labelled synthetic and real jobs, when evaluation runs, then their scores are separate and every field/type has explicit denominators and sample sufficiency.
- Given an invoice with valid provenance, when workers finish, then polling and completion delivery expose identical stated fields and recorded policy/check outcomes.
- Given a transposed total, when checking runs, then review names the check, field, expected and actual without changing the extracted total.
- Given disagreement between two or more source documents, when finalization runs, then one conflict includes all sources and blocks job pass.
- Given retry, unavailable OCR/provider, or unreadable evidence, when processing runs, then results remain consistent and failures cannot become pass decisions.

## Implementation Notes

Investigation only. Working tree was clean on `main` at `7b9975c38efa0f3e08dcb4e976303abc1ddf310e` before this draft. No application code or policy was changed. Real-document evaluation remains unavailable pending corpus intake.

Implemented reusable service evaluation with complete-job manifests and origin separation; invoice worker lifecycle, fourteen deterministic checks, fail-closed policy, retained evidence and separate review tracks, conflict detection and polling/webhook projections. Synthetic fixture results do not establish real accuracy.

Matrix audit: corpus validation/report separation is covered by test_evaluation; invoice/policy persistence, mixed unreadable pages, stale leases, conflicts and webhook parity by test_invoice_conflicts; arithmetic and identifiers by test_invoice_gate_conflicts. Implementation verification reported 347 unit/contract tests and 107 PostgreSQL integration tests passing, plus Ruff format/check and mypy.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: early dead-task completion | high | queue terminalise_dead_task marks submission failed immediately; finalize skips terminal submissions, losing sibling completion/conflicts. Patch lifecycle ordering. |
| Blind 2: dead-task lock order | high | Failure writes lack job-first locking while extraction locks job then task; caller mark_failed flush can invert lock order. Patch shared ordering. |
| Blind 3: duplicate-only completion | high | ingest duplicate branch calls mark_completed despite retained open review counts. Patch existing completion handling. |
| Blind 4: optional disagreement blocks pass | medium | INV-012 includes every disputed field, including invoice.note. Patch explicit critical paths and advisory remainder. |
| Blind 5: Decimal context rounding | high | Unbounded decimal operands exceed default 28-digit context in multiplication/sums. Bound precision and use exact local arithmetic context. |
| Blind 6: incomplete labels | high | Only emitted prediction fields are checked against labels; omitted truth and omitted prediction disappear together. Validate scalar and declared row label coverage independently. |
| Blind 7: predicted document type | medium | evaluate never compares predicted type against label type. Record classification accuracy and reject malformed types. |
| Blind 8: unused unreadable truth | medium | LabelDocument.unreadable is never scored; add labelled outcome precision/recall. |
| Blind 9: full-page provenance scores | medium | Enclosure alone accepts whole-page box for any field. Add localization constraint and regression. |
| Blind 10: excess rows hidden | medium | min(expected,actual) conceals extra predicted rows. Add row precision/excess/exact count metrics. |
| Edge 1: sibling completion | high | Same demonstrated early dead-task completion defect as Blind 1; grouped patch. |
| Edge 2: failed file skips conflicts | high | finalize returns before detection when any file failed, even if other extracted documents disagree. Patch reconciliation before failed completion. |
| Edge 3: Decimal context | high | Same arithmetic rounding defect as Blind 5; grouped patch. |
| Verification 1: arithmetic negative branches | medium | Filed search evidence shows only payable mismatch tested, leaving INV-002/003/004 unprotected. Add parameterized negative checks and enabled-gate assertions. |
| Verification 2: tolerance propagation | medium | No persisted nondefault tolerance is tested through extraction/finalization. Add PostgreSQL boundary cases. |
| Verification 3: conflict score verification | medium | Evaluator tests assert neither false-positive nor false-negative conflict rates. Add separate-origin mixed outcomes. |
| Parent 1: portable source geometry | high | extract_segment persists conditioned boxes verbatim; no inverse mapping occurs in extraction or result projection. Map every field and alternative back to source coordinates before persistence, preserving values. |
| Parent 3: stale currency registry | medium | Compared against SIX List One publication 2026-09-17: XAD/XCG missing, five retired codes included. Patch current enum snapshot and record official source/date with regressions. |
| Parent 2: schema constraint drift | medium | Migration defines conflicts status CHECK but ConflictRecord metadata omits it; align ORM constraint and verify migration drift. |

## Verification

- `uv run pytest tests/unit tests/contract` — all tests pass without live providers.
- `uv run pytest tests/integration` — PostgreSQL lifecycle, policy, persistence, tenant isolation and retry tests pass; report unavailable infrastructure explicitly.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` — pass.
- Run the eval CLI on synthetic complete-job fixtures and inspect both report formats; never present these as real accuracy.


Final verification (2026-11-03): 367 unit/contract tests and 113 migrated PostgreSQL integration tests passed. The tightened exhausted-extraction-first regression also passed independently (1 test). Ruff check, Ruff format check, mypy and git diff whitespace checks passed. All changed text files decode as UTF-8. Existing Starlette/httpx deprecation warning remains.

All nineteen review/parent findings were patched; duplicate findings shared a fix and none were deferred. Additional failure-first preparation coverage verifies no premature completion snapshot and retained conflicts between surviving invoices. Decimal precision is bounded, currency enums use the SIX 2026-09-17 snapshot, optional note disputes are advisory, and public provenance is inverse-mapped to original source pages, including alternatives and extensions. Effective tolerance is tested through persisted policy and worker processing.

Final synthetic evaluation CLI produced JSON and Markdown reports under the system temporary directory, with complete-job-v2 origin-separated metrics, all registered types/sample counts, explicit absent real corpus, and no ranking for the two synthetic documents. Raw-file scratch-database execution uses the documented injected adapter contract; the bundled offline adapter exercises reusable extraction/check/decision/conflict services. No real model accuracy, calibration, live provider call or real-document upload is claimed.
