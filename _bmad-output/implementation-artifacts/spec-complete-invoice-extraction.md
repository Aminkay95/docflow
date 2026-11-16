---
title: 'Complete commercial and proforma invoice extraction'
type: 'bugfix'
created: '2026-11-11'
status: 'done'
route: 'dispatch'
baseline_commit: 'bc47dd57afa6c2e5eabe750355653ac64c24df12'
review_loop_iteration: 0
context: ['docs/06-document-schemas.md']
---

<frozen-after-approval reason="human-owned intent â€” do not modify unless human renegotiates">

## Intent

**Problem:** Compact invoice requests prohibit optional fields and rows, and good digital pages can finish with required scalars alone. Commercial and proforma line items are therefore omitted despite their prompts requesting them.

**Approach:** Implement the approved shared checklist through explicit header/party, bounded line-item, and totals/tax extraction stages. Always inspect line-item completeness for both types, including small digital tables. Extract only stated values; missing information requires review.

## Boundaries & Constraints

**Always:** Capture stated identity/dates/currency; seller/buyer/ship-to names, addresses and identifiers; references, payment/Incoterm terms; shipment details; every source line's description, quantity, unit, price, amount, product code, HS/origin, discounts and taxes; totals and tax breakdown. Proforma additionally captures validity. Respect current type-specific allowed paths. Preserve exact source strings, source identity, independent row geometry/counts, per-cell provenance, disagreements, partial evidence and review corrections. Printed line numbers are optional; internal row indices are structural. Keep arithmetic/normalization in code, separate from extracted values.

**Never:** Call DeepSeek or run live extraction during this work, invent absent totals/rows, silently truncate, classify a header-only invoice as complete, weaken provenance/arithmetic gates, or overwrite the main checkout's unrelated work. No database migration, new dependencies, historical-result rewrites or remote push.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected behavior | Failure handling |
|---|---|---|---|
| Small digital table | Text page, dense_table=false | Explicit line stage; source rows/cells retained | No scalar-only completion shortcut |
| Scan / mixed source | Valid OCR spans and physical rows | Reader-specific citations and bounded cell reconstruction | Invalid references rejected |
| Both types | Same source field family | Correct invoice/proforma prefixes, optional fields and item codes | Current proforma exclusions remain |
| Long/multipage table | More rows than one call | Bounded batches, global row identity, deterministic merge | Gaps/truncation explicit |
| Partial or absent rows | Missing quantity/price or no verified table | Preserve valid header/cell evidence and mark review | No invented zero-row claim or guessed cells |
| Taxes/arrays | Stated tax breakdown/container arrays | Capture supported indexed paths | Do not treat every array as line_items |
| Provider failure | Scripted timeout/truncation/budget exhaustion | Retain completed stages and source evidence | Repair only affected stage within budgets |
| Other documents | Non-invoice reader contracts | Existing behavior remains compatible | No invoice rules applied |

</frozen-after-approval>

## Code Map

- `src/trade_extract/services/readers/base.py`: scalar-only compact contract and partial model currently enforce complete emitted rows.
- `src/trade_extract/services/readers/{text_layer,paddle,vision}.py`: reconstruct exact evidence, reader identities and existing native crop batches; extend scoped contracts rather than duplicate provenance logic.
- `src/trade_extract/services/{extraction,tables}.py`: local scalar shortcut, independent inventory, semantic table association, bounded rerun/reconciliation and partial retention.
- `src/trade_extract/providers/prompts/{__init__,02-commercial-invoice,03-proforma-invoice}` and `services/stages.py`: conflicting scalar-only overrides must match actual phase schemas.
- `src/trade_extract/schemas/{invoice,proforma,base}.py`: shared fields; optional arrays differ from declared tables. Review helper already displays optional row columns.

## Tasks & Acceptance

- [x] `schemas/invoice.py`, `schemas/proforma.py`: add optional `line_items[].item_code`; retain existing canonical paths and schema tags as an additive field-pack extension.
- [x] `services/readers/base.py`, scoped invoice stage helpers if needed: define phase contracts with optional scalars/indexed fields and bounded row ranges; prohibit out-of-batch paths.
- [x] `services/readers/{text_layer,paddle,vision}.py`: reconstruct permitted indexed cells/arrays with real provenance and applicable table metadata; reject duplicate/unknown citations.
- [x] `services/extraction.py`, `services/tables.py`, `config.py` if needed: require invoice row inspection regardless of density; use existing structure/crops and bounded stages; retain partial rows and fail completeness honestly. Do not mark every invoice page dense merely because of its type.
- [x] Prompt files and `services/stages.py`: align checklist/phase instructions; preserve source text rather than force model date/currency normalization. Document any new stage-size setting in `.env.example` and Compose.
- [x] Tests for schemas, compact contracts, extraction, table budgets/geometry and invoice review: cover the matrix for both types with scripted providers. Parent applies the reviewed patch to the main checkout preserving unrelated changes.

**Acceptance Criteria:**
- Given either invoice type with a non-dense table, when extracted, then every verified source row is retained and no header-only fast path hides the table.
- Given stated optional party/item/tax fields, when their phase runs, then they are permitted and reconstructed with valid provenance.
- Given incomplete rows, missing structure or provider failure, when extraction ends, then useful evidence survives with explicit review status and diagnostics.
- Given long tables, when staged, then calls stay bounded and missing/duplicate/out-of-range rows cannot be reported complete.

## Implementation Notes

The user approved the preceding checklist by asking to fix it; no intent gaps remain. Work only in this worktree. The original checkout has unrelated uncommitted changes. Keep schema tags 1.0 and add only optional item_code, without renaming required paths. Reuse inventory/crop validation; ambiguous structure stays reviewable. Incomplete emitted rows need partial validation without relaxing final completeness. Optional arrays need bounded handling independently of table inventory.

Tests must use mock/scripted providers or HTTP MockTransport. This worktree intentionally has no .env/API key. Use the original checkout's `.venv/Scripts/python.exe` and tools with PYTHONPATH pointing at this worktree's src and root; do not resync that shared environment or run workers against the live database. An isolated test database is allowed. Do not copy .env into this worktree.

Implementation handoff edits/tests only this worktree, then reports and stops. Parent handles review, merge-back and local runtime update; do not alter the original checkout or its Git index.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence and route |
|---|---|---|
| Blind 1: swallowed crop failure | medium | Crop exception only entered diagnostics; final coverage gate ignores most crop budget codes. Patch: require review for failed required vision crops; text-only rows need no images. |
| Blind 2: lost vision truncation | high | VisionReader constructs a fresh ProviderResult without finish_reason on aggregate path. Patch: propagate truncation and regression. |
| Blind 3: changed row identities | high | Recursive union merges old paths without remapping geometry after anonymous inventory receives global indices. Patch: remap unique physical rows before union. |
| Blind 4: union skips reconciliation | high | Recursive return only model-validates retained union, bypassing reconcile. Same root cause as Blind 3; patch geometry reconciliation of union. |
| Blind 5: partial rows counted as extracted | false | Existing TableMeta defines extracted as emitted row indices and forbids overlap with unresolved. Required-cell validation still fails incomplete rows and retains review_required; changing count semantics would violate existing contract. |
| Blind 6: rejected metadata contaminates repaired stage | medium | Counts/errors mutate before reader provenance and text/OCR guards. Patch: commit attempt metadata after guards. |
| Blind 7: array-bound review | false | Finite array bound deliberately returns review at saturation rather than claiming completeness; exact32 may conservatively require review. Budget continuation is outside this bounded call contract; no silent complete overflow occurs. |
| Blind 8: segment evidence in bounded rows | low | Global compact evidence cap already yields evidence_index_truncated and review; no silent completion. Per-batch evidence optimization adds branches beyond a direct correction; reject negligible correctness claim, retain explicit budget review. |
| Blind 9: missing boundary regressions | medium | New tests lack changed-row-identity, aggregate truncation and array saturation examples. Patch alongside corresponding production fixes; existing native failure tests already check failure retention. |
| Verification gap 1: untested array guard | medium | Filed search shows only index0 fixtures; deletion of saturation guard unobserved. Patch: both-type boundary tests. |
| Edge 1: aggregated truncation | high | Same verified ProviderResult metadata loss as Blind 2. Patch in shared root cause. |
| Edge 2: rerun row reconciliation | high | Same verified recursive union bypass as Blind 3/4. Patch in shared root cause. |


## Verification

- Focused schema/reader/extraction/table/provenance tests for both types, optional fields, incomplete rows, batches and failure retention; no real network transport.
- Existing review line-item tests and isolated integration checks; Ruff and relevant mypy.
- Verify main-checkout changes are preserved when merging back. No paid smoke run or document reprocessing is authorized.



### Completed verification

- Parent independent final unit/contract run: 466 passed, one ambient mail-configuration test deselected; that test passed separately with process-local fake mail configuration (no send).
- Main merged checkout: 17 local-extraction/worker tests passed, including preserved user priority tests.
- Main isolated database review/document-coverage integration: 36 passed using trade_extract_invoice_contract_test; no production data or live providers.
- Ruff passed; mypy passed for six relevant source files; diff check clean.
- Three independent review layers completed; five shared production/test fixes applied and verified. No deferred findings.
- Applied to main checkout with unrelated edits preserved, resolving one shared fixture conflict by retaining the user's reusable fixture and added tests while adopting mandatory invoice phase expectations. Git index untouched.
- No DeepSeek calls, historical job reruns, worker restart/deployment or remote push. Runtime activation is intentionally pending to avoid processing queued jobs against a live provider.
