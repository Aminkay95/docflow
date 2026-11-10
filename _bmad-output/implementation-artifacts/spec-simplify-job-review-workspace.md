---
title: 'Simplify job details into a review workspace'
type: 'feature'
created: '2026-11-05'
status: 'done'
route: 'dispatch'
baseline_commit: 'd7bd31aa72c312b0b4ec43983443212e210d97f1'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="User explicitly approved this plan and requested implementation">

## Intent

Make job details focus on readable field names, current values and source pages, with review directly on this page. Desktop has fields left and source right; smaller screens stack. Keep compact job reference/status/remaining reviews and Done / Push result. A document selector shows type/status, initially selecting the first needing review or the first document. Field selection shows its page and highlights available evidence; previous/next page controls remain available. Show corrections immediately and original values on demand; corrections require a reason. Confirm remaining fields approves only unreviewed fields of the selected document. Conflicts stay visible as compact links to existing conflict review. Done remains blocked until processing/reviews/conflicts finish; preserve terminal JSON delivery.

## Boundaries & Constraints

Always preserve evidence, corrections, ownership rules, CSRF/session security and auditor read-only behavior. Retain IDs, timestamps, policy, submissions, review history and JSON behind a collapsed More details section. Remove duplicate document table/stat cards. Preserve document/page selection with query parameters and suspend auto-refresh during unsaved editing. Handle processing, empty/failed/unreadable extraction, missing boxes and expired images without blocking appropriate existing review actions. No migrations, public API changes or ERP integration. Build on the user's existing staged/unstaged work without reverting or committing unrelated changes.

## I/O & Edge-Case Matrix

| Scenario | State | Behavior |
| --- | --- | --- |
| Normal review | Unreviewed document | Readable values beside source; selection follows provenance |
| Correction | Valid edited field/reason | Existing resolution saves overlay; current value updates; source unchanged |
| Bulk confirm | Mixed reviewed/unreviewed fields | Atomic confirmations for remaining fields; reason Reviewed against source document. |
| Repeat confirm | Completed document review | Success with zero new labels |
| Ownership/security | Other reviewer assigned, missing CSRF or auditor | Reject without saving labels |
| Partial bulk failure | One resolution fails | Roll back every label from this bulk action |
| Unavailable media | Expired/missing source image or bbox | Readable message; values and review actions remain usable |
| Incomplete job | Processing, open review/conflict | Done disabled and server continues rejecting handoff |

</frozen-after-approval>

## Code Map

- `src/trade_extract/review/routes.py`: job_detail, document workspace, history, media and resolution endpoints. Reuse read_document/CoordinateTransform/BBox and page-range authorization; add internal POST /review/documents/{document_id}/confirm-remaining. Select document with document_id and 0-based page query parameters; reject foreign job documents with 404; clamp page to selected document range. Use default first unresolved extraction review then first document. Preserve selected context after resolution reloads.
- `src/trade_extract/services/review.py`: resolve locks job then item, enforces ownership and schema, tracks reviewed fields, closes complete reviews. Add bulk helper reusing resolve under one transaction; lock job before selecting active item and reviewed fields. Skip prior confirmed/corrected paths, including extensions. Completed review repeat succeeds; failed/unreadable/empty documents use existing explicit outcome actions. Route commits once; failures roll back all labels.
- `src/trade_extract/review/templates/job.html` and static/review.js/review.css: replace verbose page with compact workspace. Existing resolution handler saves corrections; expose inline correction forms only for unreviewed fields, and existing failure/unreadable outcome form. Existing data-finish-job keeps terminal handoff. Existing highlight behavior must continue working on standalone document workspace. Keyboard field activation navigates source; keep document/page in URL via history.replaceState; page links/selector stay on current job. Native image error handlers show expired/unavailable text. Prevent refresh and warn on navigation while dirty, resume after successful save. Avoid inline handlers under CSP.
- `tests/integration/test_review_trust.py`: login/invoices fixtures, scripted pipeline and correction/ownership tests. Existing terminal handoff tests must still pass after layout text changes; meaningful assertions should verify behavior rather than obsolete headings. `tests/review_upload.test.cjs`: executable Node VM browser-script regressions; extend or add focused script/browser tests for new interactions.

## Tasks & Acceptance

- [x] Backend selected-document/page workspace data and atomic bulk confirmation service/route.
- [x] Simplified responsive template/styles, readable field labels with raw paths in tooltips, corrections/originals, compact conflicts, collapsible metadata/history/JSON.
- [x] Browser selection/highlighting/pagination, dirty-refresh protection, bulk confirmation and failure/media handling.
- [x] Integration and executable UI tests, local API rebuild/refresh and desktop/mobile visual inspection.

Given a job with multiple documents, when opened/switched, then the correct fields, current values and authorized source pages are shown with retained selection. Given a correction or bulk confirmation, when saved, then evidence and previous corrections survive and only eligible labels are created. Given assigned/invalid/read-only input, when confirmation is attempted, then authorization/validation holds and no partial transaction survives. Given open conflicts/processing, when viewed or Done attempted, then completion remains blocked. Given resolved reviews, when Done is clicked, then reviewed JSON continues reaching the terminal.

## Implementation Notes

User's explicit implementation request approves the frozen plan; prior authorization to build on dirty tree persists. No additional permission checkpoint needed. Use existing styling and dependencies; no new frontend framework. Implementation agent may use its judgment for routine UI details consistent with this approved behavior.

Implemented by implementation handoff. Shared workspace_data reuses source transforms; bulk helper locks job/item and refreshes document, skips confirmed/corrected fields and commits at route boundary. Node 8/8, focused integration 4/4, mypy and Ruff pass. Disposable real-browser integration passed desktop/mobile layout, evidence highlight, inline correction, bulk confirmation, document selection and 410 media handling; screenshots under ignored var/qa/review-workspace. Local rebuild/refresh and final independent review remain.

Final verification after review fixes: all 24 review integration tests passed, all 10 Node behavioral tests passed, Ruff check/format and mypy passed. Real Chromium browser regression passed again with final desktop/mobile screenshots visually inspected. Local API image rebuilt, refreshed and healthy; updated script served and unauthenticated confirmation rejected. All in-scope review findings patched; nine pre-existing provider/extraction follow-ups appended to deferred-work.md. No migration or ERP work. Existing staged/unstaged work preserved and no commit created because source changes are interleaved with the user's earlier work.

## Verification

- `uv run pytest tests/integration/test_review_trust.py -q` with live local test PostgreSQL, never production database.
- `node --test tests/review_upload.test.cjs` plus new focused browser-script tests if added.
- Ruff check/format check and mypy for changed Python modules.
- Inspect rendered desktop/mobile workspace, field/page navigation, correction and confirmation flows. Rebuild and refresh only the local API service after verification.

## Review Triage Log

- Blind 1 / Edge 3 — medium, defer: DeepSeek._call compares each request with the full ceiling; no cumulative reservation is present. Pre-existing local-first extraction work, not changed by this workspace.
- Blind 2 — medium, defer: _BudgetedProvider claims one method call while DeepSeek._call loops HTTP attempts internally. Pre-existing provider budgeting issue.
- Blind 3 — medium, defer: input estimates use prompt/evidence but omit previous_json/error/image components actually sent. Pre-existing preflight issue.
- Blind 4 — high, defer: provider_http_error emits response.text[:1000] under error_detail, which is not redacted. Pre-existing diagnostic-content exposure outside workspace changes.
- Blind 5 — medium, defer: evidence_lines strips text but retains untrimmed characters, and extract_local uses stripped offsets against those characters. Existing local geometry issue.
- Blind 6 — low, rejected: OCR evidence intentionally lacks character geometry and remains provider evidence rather than local truth. Approximate geometry would weaken conservative evidence rules; not a workspace defect.
- Blind 7 — maybe-false medium, defer: compact_value_schema allows required scalar paths and no indexed templates. Whether separate table/optional stages compensate requires an extraction-stage audit; workspace does not alter this contract.
- Blind 8 — medium, defer: TextLayerReader fields[path] assignment can overwrite conflicting duplicate paths. Existing reader issue.
- Blind 9 — medium, defer: ProviderError exits staged reader loops immediately, including truncation. Existing repair behavior needs an extraction fix.
- Blind 10 — medium, defer: retain_partial builds tables={} despite retained indexed candidates. Existing partial-table issue.
- Edge 1 — medium, patch: saving one form resets the dirty flag before reload despite other dirty forms. Require explicit discard confirmation and regression coverage.
- Edge 2 — medium, patch: retained partial correction snapshots fail full-schema required scalar checks. Reuse partial validation for incomplete snapshots while retaining supplied-field constraints; test valid correction and empty-required rejection.
- Verification 1 — medium, patch: Node regressions were run here but are absent from README verification commands. Document command/runtime.
- Own 1 — medium, patch: native document selection changes before discard dialog; cancel must restore the rendered document selection.
- Own 2 — low, patch: raw job ID remained in the visible subtitle and job reference was duplicated; use Job details title, keep IDs under More details.
- Own 3 — low, patch: resolved conflicts add clutter to primary notice; show open conflicts there and retain full JSON details.
- Own 4 — low, patch: Needs review indicator is nested under write permissions; retain status for auditors while hiding controls.
