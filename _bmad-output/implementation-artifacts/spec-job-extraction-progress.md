---
title: 'Live extraction progress on job details'
type: 'feature'
created: '2026-11-11'
status: 'done'
route: 'dispatch'
baseline_commit: '4594b48ceb608a8913eac50e344070faf1024b68'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The job details page displays a generic spinner while extraction runs, leaving users unable to tell what is happening or when they can review results.

**Approach:** Implement the user's approved live stage timeline and loading-state proposal. Display honest backend-reported progress, then transition to the existing review workspace when processing ends. The user's instruction to implement the preceding proposal authorizes this implementation; routine technical choices below introduce no new product decision.

## Boundaries & Constraints

**Always:** Use real persisted worker state; distinguish processing from review and failure. Show elapsed time and last stage update, filenames, and available source previews. Explain that processing continues after leaving. Keep corrections, selected document/page/tab, read-only permissions, authentication, and existing completion guards intact. Handle multiple files/documents and retries. Preserve partial results. Use existing design styles, accessible status text, keyboard controls, and reduced-motion support.

**Never:** Invent percentages or stage durations, claim optional OCR/LLM work happened when skipped, represent a polling failure as an extraction failure, discard unsaved edits, leak raw diagnostics or document values in progress metadata, or weaken validation. No new extraction retry mechanism, schema migration, remote deployment, or push; retain existing upload/review recovery actions.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| New job | Queued task, no documents yet | Filename and waiting/preparing state; preview placeholder until available | No empty-review failure message |
| Active extraction | Real preparation, identification, reading, extraction, validation boundaries | Current stage spinner, completed observed stages, elapsed and last-update text | Long-running work stays processing |
| Multiple documents | Completed/reviewable document alongside pending sibling | Aggregate counts and truthful active stage; existing review remains available | Do not mark the whole job ready early |
| Optional work | Local-only completion or classification disabled | Accurate stages without fabricated provider activity | Unrecorded historical stages have no invented timestamps |
| Ready for review | All extraction/finalization terminal, open review/conflicts | Extracted-field/review counts, review action, collapsed processing details | Clean completed jobs say complete/ready rather than requiring review |
| Worker failure/retry | Failed task, ownership changes, retry or new submission | Human-readable failure/current retry stage; retained results usable | Stale attempts cannot update progress |
| Draft edits | Progress changes or completes while editing | Update timeline without replacing forms; defer workspace refresh until safe | Preserve selection and unsaved input |
| Poll interruption | Timeout, offline response, expired session, hidden tab | Retain last state, explain reconnection/session requirement, resume safely | No overlapping requests or endless unbounded fetch |

</frozen-after-approval>

## Code Map

- `src/trade_extract/db/models/task.py`: payload JSONB, attempts and owner; no migration.
- `workers/runner.py`, `workers/handlers/{prepare_file,classify_segment,extract_segment}.py`: claim ownership and actual phase boundaries. Progress writes use separate short transactions.
- `services/extraction.py`: optional callback around reading, extraction and checks; standalone callers remain compatible.
- `review/routes.py::job_detail`, `review/templates/job.html`: existing selection, review counts and completion guards; add shared progress projection and authenticated no-store endpoint.
- `review/static/{review.js,review.css}`: reuse styles, `dirtyReviewForms`, source selection and URL context; replace job reload polling only.
- `tests/integration/{test_review_trust,test_worker_failures}.py`, `tests/review_upload.test.cjs`: auth, ownership, workspace and executable browser harnesses.

## Tasks & Acceptance

- [x] Add bounded, content-free progress under task payload. Preserve request keys, assign fresh JSON, guard running status/owner/attempt, and reset stale attempt state.
- [x] Instrument real worker/extraction boundaries; use optional callbacks and independent short transactions. Derive terminal readiness from documents, submissions and review state.
- [x] Add shared projection and authenticated no-store progress GET endpoint; cover multi-document, historical, failure and pending-finalization states.
- [x] Implement responsive timeline, filename/preview states, elapsed/update text, review action and collapsed processing details.
- [x] Add draft-safe polling with visibility/network handling, bounded fetches, terminal stop and safe workspace refresh. Preserve other pages' refresh behavior.
- [x] Verify automated Python/browser behavior and update local API/worker source; attempt visual inspection and record blocked checks.

**Acceptance Criteria:**
- Given a blocked provider, when another session polls, then extraction progress is already visible.
- Given stale task ownership, when an old worker reports, then current progress is unchanged.
- Given pending sibling/finalization work, when polled, then the job remains processing and completion stays blocked.
- Given a dirty correction, when processing completes, then the draft and selection survive without forced navigation.
- Given terminal review, success or failure, when rendered, then distinct truthful states and existing authorized actions appear.

## Implementation Notes

The preceding proposal was approved by the user's implementation instruction. No intent gaps remain. Use existing review-item counts rather than invented field-flag counts. Historical jobs get only known coarse phases. Server timestamps own stage changes; client clocks only animate elapsed display. No schema/dependency changes or paid extraction calls are needed.

Implemented by the step-03 handoff, followed by parent verification fixes: ignore stale queued/reclaimed metadata, prioritize pipeline work over finalizers, keep ready summary outside collapsed history, move initial reporting inside worker retry handling, use actual submission completion times rather than later review updates, and avoid announcing unchanged status text repeatedly. Review action focuses the existing workspace without navigation. Explicit mixed-document, local-only and historical coverage added.

Final automated verification: 82 Python tests passed across progress, existing review/worker behavior, extraction and local-only extraction; all 20 executable browser-script tests passed. Ruff and mypy on seven source modules passed. Review patches stabilized workspace refreshes, isolated field counts in PostgreSQL, preserved offline text, and covered production extraction-handler forwarding. Timers use the latest upload start and the latest reliable completion across concurrent work. Local API, worker and maintenance source layers updated without migrations or paid provider calls.

Final runtime verification: API health 200, unauthenticated progress request 401, running projection source SHA-256 matches the workspace. Existing recovery job projects review state, processing=false, 20 fields and one review; historical stage update is honestly unavailable. All three local application services restarted successfully. Both review follow-ups are recorded in deferred-work.md: terminal pages require refresh for later work initiated elsewhere, and manual desktop/mobile appearance verification remains unavailable.

Visual QA limitation: the browser access host was unavailable. Native CUA inspection then stopped at its safeguard because it could not confidently determine the Windows browser URL. No further UI actions or alternate UI automation attempted. Desktop/mobile screenshot inspection remains a deferred manual validation; no visual-verification claim is made.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and disposition |
|---|---|---|
| Blind 1: phase completion can imply all siblings finished | medium | A passed phase was labeled complete while sibling work remained. Patched partial history state and scoped wording, with multi-document coverage. |
| Blind 2: routine worker starts reload the page | medium | Pending document status/updated_at and file status changed the workspace version. Patched stable pending signatures and kept material result/review changes observable; regression covers the distinction. |
| Blind 3: old job creation inflates a later upload timer | medium | The panel used job.created_at for subsequent submissions. Patched latest submission start and explicit latest-upload wording, with coverage. |
| Blind 4: failed work lacks duration despite persisted failure time | low | Task.finished_at can provide an end when a submission lacks completed_at. Added failure-time fallback while retaining unavailable duration when no reliable timestamp exists. Concurrent uploads use the latest actual completion across the job. |
| Blind 5: terminal pages do not detect later work initiated elsewhere | low | Baseline pages also polled only while processing. Deferred this pre-existing enhancement; refresh/reopen a job when new work is submitted after its page has reached a terminal state. Active processing updates and terminal polling stop remain intentional. |
| Blind 6: abort during offline overwrites offline explanation | low | The generic catch ran after intentional offline abort. Patched the online/visibility guard and added an in-flight offline regression. |
| Blind 7: polls hydrate full extraction JSON | medium | ORM document loads transferred and decoded values just to count them. Patched lifecycle-column projection and server-side PostgreSQL field counts; tests cover absent/populated data and no Document hydration. |
| Blind 8: responsive/accessible rendering lacks real-browser verification | medium | Visual verification is blocked by the CUA URL-verification safeguard. Deferred manual validation rather than claim screenshots or layout verification. DOM/focus tests and reduced-motion CSS do not replace that inspection. |
| Verification gap 1: test bypasses production extraction-handler forwarding | medium | The initial callback test used a custom prepare handler. Added an injected-provider test through the production extraction handler asserting live extracting visibility and subsequent validation, without paid calls. |

## Verification

- Isolated-database progress/ownership, blocked-call visibility, projection, authentication and review/worker integration tests.
- Executable browser tests for progress, draft fields/line items, terminal refresh, network/visibility and retained context.
- Ruff/relevant type checks and local runtime source/service health; desktop/mobile visual inspection attempted but blocked, documented for manual follow-up.
