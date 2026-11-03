# Evaluation corpus intake and execution

The corpus unit is a **complete caller-grouped job**. Each `*.labels.json` manifest
requires a unique `job_id`, explicit `origin: real|synthetic`, `complete: true`,
all document identities and relative evidence paths, a field label map, row counts,
and the exact expected canonical conflict keys. Present labels require stated text,
its declared comparison type, page index and normalized top-left box. Label absent
fields explicitly with `value: null`; do not attach boxes to absence. Every emitted
prediction must have a label. Unknown paths, malformed dates/numbers, duplicate IDs,
negative rows, path traversal, absolute paths, Windows drives and escaping symlinks
are refused. Corpus files remain local.

Run the authored synthetic service corpus without credentials or network calls:

```powershell
uv run python -m scripts.eval tests/fixtures/evaluation/synthetic --pipeline scripts.eval_synthetic:run --output "$env:TEMP/trade-extract-evaluation"
```

The adapter reads separate authored evidence, builds a scripted mock reader response,
and invokes `services.extraction.extract`, invoice checks, policy decisions and
canonical conflict detection. It never reads truth values as predictions. The two
invoices intentionally omit line rows: extraction succeeds, INV-001 requires review,
and different currencies create one conflict with every source. These small fixtures
exercise contracts; they cannot measure OCR/model accuracy or tune any threshold.

For a complete raw-document run, inject a callable `module:function` accepting
`(LabelJob, corpus_root)` and returning `documents` keyed by the manifest's document
IDs plus a list of detected canonical conflict keys. Use the application's ingest,
worker and result builder in a dedicated **scratch PostgreSQL database and temporary
storage directory**. Migrate the scratch database to head, seed an isolated tenant and
versioned policy, submit every file from one manifest as one group, drain the worker,
and translate generated document identities back to labels by source file and page
range. The callback must execute extraction, not load a saved prediction report.
Tests in `tests/integration/test_invoice_conflicts.py` exercise that lifecycle with
injected providers against migrated PostgreSQL. No bundled adapter enables live
provider calls; those require explicit authorization and a separately supplied corpus.
Never point an evaluation adapter at production storage or database state.

The evaluator writes `evaluation.json` and `evaluation.md`. Origins are separate
throughout; missing real data is reported as **real accuracy and calibration
unmeasured**. Per-field exact match, precision, recall and provenance enclosure rates
have explicit hits and denominators; zero denominators produce null, not perfect
scores. Per-type reports add row completeness, review/pass/unreadable rates and
sample counts. Conflict precision/recall is computed across complete jobs. Proportions
include 95% Wilson intervals. Type rankings are refused below twenty documents and
field sufficiency is explicit. This is descriptive evidence, not automatic policy
promotion. Output reports may contain sensitive field names and belong outside git.

Implemented limits: exact decimal arithmetic accepts unambiguous dot-decimal strings;
no locale or currency conversion is guessed. English amount-in-words support is
conservative and unsupported forms produce an advisory warning. Canonical aliases
are explicit; row-level cargo totals are not inferred or summed. `_kg` field semantics
state kilograms; a contradictory explicit unit is an incomparable-unit review reason.
The remaining document types stay full review without lifecycle extraction activation.
Real corpus intake, model calibration and real invoice accuracy remain pending.

The currency gate uses the SIX ISO 4217 current-list snapshot published 2026-09-17:
https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml .
It includes XCG and XAD and excludes retired ANG, BGN, CUC, SLL and ZWL codes.
Field labels must cover every scalar and every declared row cell, with explicit
absence and zero row counts. Reports include classification accuracy, unreadable
precision/recall, row precision, excess rows and exact row-count agreement. Provenance
hits require enclosure and an IoU of at least 0.5, preventing full-page boxes from
scoring as localized text evidence. Decimal work is limited to 256 digits per operand
and checks/comparisons use sufficient local precision for exact supported arithmetic.

Raw-document label boxes refer to original rendered source pages. Worker snapshots
inverse-map conditioned reader evidence before reporting it; a custom pipeline adapter
must return predictions in that same source frame for meaningful provenance metrics.
