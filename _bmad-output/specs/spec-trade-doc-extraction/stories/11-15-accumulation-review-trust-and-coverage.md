---
title: 'Stories 11–15: accumulation, review, trust and document coverage'
type: 'feature'
created: '2026-11-03'
status: 'done'
baseline_commit: '7ec6e273c4454eb86189c654e034c973d93d994f'
route: 'dispatch'
review_loop_iteration: 0
context:
  - 'docs/04-api.md'
  - 'docs/05-ui-architecture.md'
  - 'docs/06-document-schemas.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Accumulation lacks its complete read contract and regression coverage; reviewers cannot resolve persisted review items; trust records are absent; six registered document types are not activated.

**Approach:** Complete stories 11–15 sequentially, reusing the implemented invoice lifecycle, schemas, policy and conflict evidence. Deliver authenticated review workflows and retained labels, conservative trust scaffolding and review-only extraction for the remaining types.

## Boundaries & Constraints

**Always:** Keep all five goals, as approved. Scope jobs by client and opaque reference. Preserve source hashes, provenance, immutable extraction snapshots, submission-specific results, idempotency and whole-job reconciliation. Reopen completed jobs and clear terminal timestamps on new submissions; reject failed jobs as documented. Reconcile retained extractions after source purge. Maintain independent extraction/conflict queues and counts. Record reviewer, field, prior value, outcome, reason and timestamp for each resolution; corrections are retained overlays. Conflict decisions retain every stated source and never rewrite evidence. Require session authentication, CSRF, roles, epoch revocation and protected media. New trust identities start at full review; corrections demote only their matching record. Every processed document resolves to a retry-safe trust observation. Remaining types always require full review. Read printed customs amounts only; report disagreements with comparable invoiced components with source evidence and explicit incomparable diagnostics.

**Never:** Infer shipment grouping, compute customs values or taxes, promote trust automatically, reduce v1 review depth from trust, fabricate missing fields, emit unprovenanced leaves, modify the approved story-3 contract silently, upload real documents, call live providers, deploy or implement unrelated administration screens.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Accumulation | Later submission, same client/reference | One job, all retained sources compared | Tenant isolation; failed job rejected |
| Replay/concurrency | Repeated files or overlapping workers | Stable evidence and accurate counters | Job-first locking; stale work rejected |
| Extraction review | Correct, confirm or unreadable confirmation | Immutable label and correction overlay | Required reason; validate field/provenance; stale resolution 409 |
| Conflict review | Accept source, waive or notify client | Separate business decision | Notification keeps item open; invalid source rejected |
| Trust | New fingerprint or human correction | Full-review identity or matching demotion | Unknown supplier/fingerprint handled conservatively |
| Coverage | Any of six remaining registered types | Validated extraction and full review | Unreadable pages skipped; unsupported types remain explicit |
| Browser security | Missing/expired session, invalid CSRF, auditor mutation | Protected review/media routes | Reject before mutation |

**Approved decision:** Preserve all existing schema versions, typed primary field paths and string-only extension contracts for story 15. This explicitly overrides its extension-only placement requirement. Implement stories sequentially.

</frozen-after-approval>

## Code Map

- `services/ingest.py`, `db/repositories/jobs.py`: job reuse, reopen, deduplication and submission result assembly exist; extend with job read service/routes.
- `workers/handlers/finalize_submission.py`, `db/repositories/evidence.py`: whole-job reconciliation, immutable decisions, distinct review items/counts; harden replay and resolved-conflict behavior.
- `db/models/evidence.py`, `reviewer.py`, `document.py`: reuse review, reviewer and identity columns; add resolution/trust/overlay persistence and migration after `0004_evidence.py`.
- `api/app.py`, `api/deps.py`: integrate internal review router, auth, protected media and job API; preserve heavy-library import boundary.
- `workers/handlers/prepare_file.py`: currently activates commercial invoices only; enqueue all seven registry types.
- `workers/handlers/extract_segment.py`: generic extraction and inverse provenance mapping already exist; restrict invoice checks to invoices and attach trust records.
- `services/extraction.py`, `schemas/registry.py`, seven schema modules: reuse validation/repair, routing and registry; resolve local contract question before edits.
- `domain/canonical.py`, `services/conflicts.py`: extend explicit comparisons to customs printed amounts with currency/component compatibility.

## Tasks & Acceptance

**Execution:**
- [x] `services/jobs.py`, `api/routes/jobs.py`, API schemas/OpenAPI — complete documented job reads and accumulated evidence.
- [x] `services/ingest.py`, `workers/handlers/finalize_submission.py`, `db/repositories/evidence.py` — harden concurrent accumulation and stable conflict history.
- [x] `db/models/review.py`, `db/models/trust.py`, `migrations/versions/0005_review_trust.py` — retained labels, overlays, assignments and retry-safe trust observations.
- [x] `services/review.py`, `services/trust.py` — transactional resolutions, counters, terminal transitions and demotion.
- [x] `review/`, `scripts/`, `config.py`, `.env.example`, `pyproject.toml`, `uv.lock` — authenticated two-track console, provisioning and packaged assets.
- [x] `workers/handlers/prepare_file.py`, `extract_segment.py`, `domain/canonical.py`, `services/conflicts.py`, applicable schemas — activate remaining types and evidenced reconciliation.
- [x] `tests/unit/`, `tests/integration/`, `tests/contract/` — matrix, migration, auth, lifecycle, concurrency and per-type coverage tests.
- [x] `docs/`, story implementation records — document final behavior, limitations and validation.

**Acceptance Criteria:**
- Given two submissions separated in time, when processing finishes, then one scoped job exposes both documents and cross-submission conflicts.
- Given separate open tracks, when a reviewer resolves either, then labels persist and only the appropriate counts/state change.
- Given a matching trust identity, when a correction is recorded, then that identity demotes and unrelated identities remain unchanged.
- Given any registered remaining type, when extraction succeeds, then every emitted leaf has provenance and its document enters full review.

## Implementation Notes

Implemented sequentially: accumulated reads and retained conflict fingerprints (11), authenticated two-track review and immutable labels/overlays (12), retry-safe conservative trust identities and scoped correction demotion (13), activation of proforma/B/L/custom entry (14), then delivery order/port pass/memo (15). Existing schema paths, versions and requiredness are unchanged by explicit approval.

The invoice lifecycle and conflict engine already existed at baseline. All seven registry types now use the same extraction/repair/provenance path; invoice checks are restricted to commercial invoices. Custom-entry comparisons use printed matching components and currency evidence only. Identical purged files with retained snapshots/labels stay duplicates. Review gate snapshots are immutable; resolved status, correction overlays and separate open counts show subsequent human action. Unknown trust identities are isolated per document, PostgreSQL upsert handles shared creation, and row locks serialize correction counters. No automatic promotion or reduction in v1 review depth exists.

Runtime uses the existing SECRET_KEY and environment for signed reviewer cookies; no separate configuration settings were needed. Added Jinja2/itsdangerous dependencies and verified the built wheel contains all review templates/static assets. Migration 0005 adds labels, assignments and trust observation persistence. Internal operators span tenants, but customer keys cannot access review and service writes verify client identity. Login throttling is process-local; aggregate limits belong at the reverse proxy for multiple processes.

Matrix audit: accumulation/expiry/replay and tenant isolation in test_accumulation plus existing lifecycle/conflict tests; corrections/extension fields/stale claims and independent tracks in test_review_trust; trust identity isolation, retry and concurrent creation in test_review_trust; per-type worker persistence/full review in test_document_coverage and reader refusal in test_remaining_types; browser auth, CSRF, roles, epoch and idle expiry in test_review_trust. Focused story 11 checks: 15 passed; initial review/trust checks: 4 passed; six coverage lifecycle cases passed; reader/conflict cases: 42 passed. Full unit/contract suite: 374 passed. Full PostgreSQL run: 126 passed, two old assertions corrected and their focused rerun passed. Final verification will be recorded after review fixes.

Dashboard screenshot visually inspected; workspace HTML rendering/authenticated routes are exercised by tests. Additional workspace browser preview could not run: shell browser launch was blocked by tool policy and the browser access host is unavailable. No real document uploads, live provider calls, deployment or remote operations were performed.

Final review patches add SQL keyset collection pagination, 100-item review queue navigation, source-type conflict filtering, fingerprint-based stale decision rejection, visible conflict/reviewer history, unsuccessful progress preservation, expiring thread-safe login buckets and malformed signed-session rejection. Shared trust demotion counters and a concurrently failed job are covered with real PostgreSQL transactions. The final diff was regenerated and inspected after fixes. No findings were deferred.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: missing public contract | false | Baseline docs/openapi.yaml already defines job/document endpoints, collection schemas and acceptance job_url; the new corrections property is documented. |
| Blind 2: inaccessible queue after 100 | medium | Verified fixed limit hid later items. Patch internal cursor navigation; 101-item test reaches the last item. |
| Blind 3: collection materializes entire job | medium | read_job loaded unrelated evidence before slicing. Patch DB keyset limit/filter and load only selected conflicts. |
| Blind 4: full overview is unbounded | low | Full summary overview is the documented contract, not a paginated response. Removed heavy source bodies from it; a new cap would break all-job semantics and is not justified for everyday overview use. Keep bounded collections for large jobs; reject incompatible cap. |
| Blind 5: stale conflict decision | high | An open conflict fingerprint changes when later evidence arrives. Patch required internal expected fingerprint and 409 on mismatch; new submission test proves stale rejection. |
| Blind 6: hidden conflict resolution history | medium | Stored labels were absent from workspace. Patch display notification, acceptance/waiver, selected source, reviewer and reason. |
| Blind 7: reviewer identity omitted | low | Identity stored but not shown. Direct patch of both history displays; route tests assert identity. |
| Blind 8: acknowledgement counts as extraction success | high | Resolved unreadable/failed rows entered completed count. Patch progress to retain failed/unreadable decision semantics; both acknowledgement cases assert zero successes. |
| Blind 9: permanent login buckets | medium | Old username buckets were never revisited. Patch global expiry on subsequent attempts, input bounds and lock; injected-clock boundary tests exercise cleanup. |
| Blind 10: irrelevant conflict input silently discarded | low | Provided source/path could be discarded. Patch reject irrelevant service inputs and send source only for acceptance in UI. |
| Edge 1: conflict document_type filter always empty | medium | Conflict has no top-level document_type. Patch source-document filter in SQL and matching/nonmatching integration cases. |
| Edge 2: unbounded collection load | medium | Same demonstrated root cause as Blind 3; grouped patch. |
| Verification 1: successful acceptance untested | medium | Add valid acceptance variant asserting chosen source, resolved status, queue counts, replay and immutable source snapshots. |
| Verification 2: throttling boundaries untested | medium | Add IP/user window limits and expiry cases using FrozenClock, including unused-bucket cleanup. |
| Parent: signed login token used as session | medium | Shared signed login string caused TypeError on session parsing. Patch shape/type rejection to 401; authentication test reuses a login token as a session. |

| Parent: concurrent failed job overwritten by stale identity map | high | Refresh the already-loaded Job under its row lock; a synchronized two-transaction regression proves a reviewer cannot revive a concurrently failed job. |

All confirmed findings were patched. No findings were deferred. The incompatible overview cap was rejected as described above.

## Verification

- `uv run pytest tests/unit tests/contract`
- `uv run pytest tests/integration` against PostgreSQL; report unavailable infrastructure.
- `uv run ruff check .` and `uv run mypy`.
- Render/inspect review pages and test media authorization, CSRF, empty queues and resolution errors.



Final verification (2026-11-03):

- uv run pytest tests/unit tests/contract -q: 374 passed.
- uv run pytest tests/integration -q: 132 passed. After the final row-lock refresh and its new regression, uv run pytest tests/integration/test_review_trust.py -q: 13 passed. Together these cover all 133 current integration cases; the 12 existing review cases were rerun with the new one.
- uv run ruff check .: passed.
- uv run mypy: passed (121 source files).
- git diff --check: passed.
- uv build --wheel: passed; templates, CSS and JavaScript confirmed in the wheel.

The existing Starlette/httpx deprecation warning remains. Dashboard visual inspection passed; additional workspace browser preview was unavailable as recorded above. All five stories are complete with existing document schemas preserved.
