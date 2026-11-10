---
title: 'Recover failed DeepSeek invoice extraction'
type: 'bugfix'
created: '2026-11-10'
status: 'done'
baseline_commit: '1d9a5756ff013ae9537dab47ae2989393b9c18dc'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Job `job_0a296ea9ee6c40c1bfe645d3ce9b1a8e` failed after two provider timeouts and a 16,384-token truncated response. Its running worker predates the staged prompts, and the seeded policy is incompatible with the gate validator.

**Approach:** Reduce OCR scalar response overhead, correctly deliver type instructions through the deployed worker, support the existing policy format safely, and verify recovery using the original uploaded document.

## Boundaries & Constraints

**Always:** Preserve exact evidence and source identity, field provenance, table completeness, disagreements, provider budgets, and explicit review outcomes. Retain useful partial extraction when escalation fails. Validate legacy policy values explicitly. Rebuild local services from current source and inspect their effective settings. Preserve original provider-call history and reviewer corrections when retrying.

**Never:** Accept incomplete JSON, invent missing values, silently drop table rows, disable checks to obtain a pass, expose credentials or document content in logs, overwrite historical policy rows, or claim extraction accuracy based only on a successful request. No remote deployment or Git push.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Scanned invoice | OCR spans with stable evidence IDs | Compact scalar output reconstructed into verified page provenance | Unmatched evidence stays absent |
| Staged prompt | Type instructions plus compact schema | Supplied response contract takes precedence; compact scalar calls cannot request full envelopes | Unknown type fails explicitly |
| Table extraction | Independent row inventory and image crops | Existing bounded table extraction and reconciliation remain available | Incomplete tables require review |
| Legacy policy | Seeded per-type review depth, limits, trust flag | Explicit supported conversion to validated gate settings | Malformed or unknown settings fail closed |
| Provider failure | Timeout or output truncation | Bounded escalation and honest partial/review outcome | No retry loop or discarded verified fields |
| Deployment | Current source and local environment | Worker has current prompt loader and configured provider timeouts | Verify runtime rather than infer from source |

</frozen-after-approval>

## Code Map

- `services/extraction.py` selects the full schema for both Paddle and vision; OCR responses therefore repeat full provenance. Reuse compact evidence indexing and partial-result retention.
- `services/readers/text_layer.py`, `services/local_extraction.py`, `services/readers/paddle.py` contain compact reconstruction, exact character evidence, and OCR span validation. Reuse these boundaries; preserve OCR whole-span provenance checks.
- `providers/prompts/__init__.py`, `services/stages.py`, and type Markdown files load instructions, but compact and full contracts need unambiguous separation.
- `domain/policy.py`, `services/decisions.py`, `scripts/bootstrap.py` disagree on the seeded configuration. Policy validation must recognize the documented legacy shape without accepting arbitrary extra keys.
- `docker-compose.yml` omits stage flags and provider timeout settings; the running image lacks the new loader. Classification also requires a task-kind database constraint update before enabling; do not enable it blindly.
- `db/repositories/tasks.py`, worker handlers, and job result services own lifecycle and retry bookkeeping.

## Tasks & Acceptance

**Execution:**
- [x] Update OCR scalar request/reconstruction and extraction routing to use compact evidence references while preserving table stages.
- [x] Clarify staged prompt response-contract precedence and add focused reader/prompt regression tests.
- [x] Add explicit legacy policy validation and canonical bootstrap output; test legacy, canonical, and malformed configurations.
- [x] Wire provider timeout and extraction-stage settings through Compose and `.env.example`; cover configuration propagation in contract tests.
- [x] Rebuild local services, verify effective runtime settings, and retry the failed source through existing lifecycle primitives after checking reviewer state.

**Acceptance Criteria:**
- Given the original scan, when extraction is rerun, then it yields verified fields or a specific remaining diagnostic, with no policy-shape error and no stale-worker ambiguity.
- Given a compact scalar response, when it cites an unknown line or unstated value, then that value is rejected by provenance validation.
- Given a legacy baseline policy, when evaluated, then invoice checks and full review for other document types retain their intended semantics.
- Given provider exhaustion, when useful fields already exist, then they remain available for review and incomplete output cannot auto-pass.

## Implementation Notes

- Compact OCR values now reconstruct whole-span geometry locally. Evidence IDs are page-local so reader subsets retain identical IDs. OCR requests do not duplicate the existing compact evidence index.
- Explicit legacy policy parsing is shared by decisions and stored-policy validation. Future bootstrap policies use the canonical shape; historical rows remain unchanged.
- Verified 107 focused tests and 44 integration tests on an isolated database. Parent reran 54 relevant tests, then 44 tests after policy-entrypoint and evidence-deduplication corrections; all passed, with Ruff clean.
- Local runtime configuration enables stage prompts and uses a 180-second request timeout. Classification stays disabled pending its missing task-kind migration.
- Retry uses normal ingestion into a separate recovery job because extraction snapshots, decisions and reviewer records have uniqueness constraints. Original job and provider history remain intact. Source bytes are verified by size and SHA-256 before resubmission; no webhook is requested.
- First live recovery had valid policy configuration and a successful OCR call (4,128 input / 4,943 output tokens, 36.6 seconds), but retained no valid OCR fields and the subsequent vision call still exhausted 16,384 tokens. Official DeepSeek documentation confirms high-effort thinking is on by default when omitted. Explicitly disable thinking for these evidence-copying provider calls so the JSON output can use the bounded budget.
- Final live recovery `job_5ecf62fff91d43e5a9357a6542bd330f` retained 20 fields and completed all worker tasks. OCR call: 4,053 input / 346 output tokens in 2.43 seconds; vision: 8,974 input / 2,642 output tokens in 9.296 seconds. No provider errors, empty extraction diagnostics, and policy_valid=true. Invoice checks correctly require review for missing/inconsistent source amounts and coverage; no automatic pass is claimed.
- Rebuilt API/worker/maintenance locally. Docker did not reuse the OCR dependency layer on a follow-up build, so final worker source was layered onto the completed dependency image using `var/recovery/Dockerfile.worker-source`. Verified running adapter and OCR-reader hashes against workspace source. Runtime stage prompts=true, timeout=180, classification=false. Original records are untouched. Full focused suite rerun: 107 passed.
- Review patches isolate validated OCR evidence on mixed-source pages, reject duplicate compact paths, restrict legacy envelope compatibility to the text reader, and cover recursive no-document fallback plus extraction-level invalid evidence. Final focused suite: 138 passed. Final isolated-database API/worker/invoice/coverage integration suite: 62 passed. Ruff and whitespace checks passed.
- Broader integration verification exposed a pre-existing fixture expecting removed character geometry and automatic dual-reader routing for clean PDFs. Updated it to consume compact evidence and explicitly exercise the comparison route, keeping its original field-agreement assertions. An initial default-credential integration invocation skipped and was not counted; the final run used project connection settings with only the database name changed.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and disposition |
|---|---|---|
| Blind 1: mixed text/OCR shadows OCR evidence | medium | `evidence_lines` prefers characters; new compact Paddle path used that index. Patched OCR-only validated indexing with a mixed-source regression. |
| Blind 2: recursive partial document loses earlier fields | medium | The baseline already returned recursive partial documents without merging first-pass fields. This change adds only the no-document fallback. Deferred the pre-existing partial-document merge issue; successful live recovery did not trigger structural redispatch. |
| Blind 3: duplicate compact paths overwrite values | medium | A repeated path assigned `fields[path]` twice. Patched explicit duplicate rejection and regression coverage. |
| Blind 4: compact Paddle accepts envelope fallback | medium | Shared compatibility fallback accepted full envelopes in the new OCR compact route. Patched Paddle rejection while preserving existing text-reader compatibility. |
| Blind 5: failed structural redispatch lacks regression | medium | Immediate exhaustion coverage did not reach the changed recursive branch. Added a late-table recursive exhaustion test retaining first-pass field provenance and review diagnostics. |
| Blind 6: legacy limits discarded during conversion | false | Upload enforcement reads Settings in `validate_submission` before and after this change; gate configuration never enforced these metadata limits. Conversion leaves historical policy rows intact and changes no upload-limit consumer. |
| Blind 7: negative OCR cases lack extraction-level coverage | low | Reader tests alone did not establish final routing. Added extraction-level unknown-ID, invented-value and substring rejection cases. |
| Verification gap 1: failed table redispatch retention untested | medium | Same branch gap as Blind 5, independently verified by reviewer. Covered by the new recursive exhaustion regression. |
| Edge 1: mixed-source Paddle citations discarded | medium | Same new index-selection defect as Blind 1. OCR-only index replacement and mixed-source test resolve it. |
| Edge 2: recursive partial result drops earlier evidence | medium | Same baseline partial-document merge omission as Blind 2; deferred as pre-existing rather than claim the new no-document fallback resolves all recursive outcomes. |

## Verification

- Run focused reader, extraction, provider, stage, and gate unit tests plus Compose contract tests.
- Run Ruff on changed Python files and relevant integration tests against the local database.
- Inspect rebuilt worker settings and prompt selection without printing secrets.
- Inspect the retried job's fields, decision diagnostics, provider usage, and terminal/review state. A review outcome caused by genuinely missing source values is acceptable; a repeated infrastructure/configuration failure is not evidence of recovery.
