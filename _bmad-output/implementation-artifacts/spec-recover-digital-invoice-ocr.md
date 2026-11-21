---
title: 'Recover readable digital invoices from OCR stalls'
type: 'bugfix'
created: '2026-11-20'
status: 'done'
route: 'oneshot'
---
<frozen-after-approval>
## Intent

Fix the latest invoice's repeated native OCR timeout, report failed jobs accurately, and verify recovered data against its original source. Keep ambiguous values in review and preserve earlier processing attempts.
</frozen-after-approval>

## Implementation Notes

Main checkout now recognizes the Item / Quantity / Rate / Amount digital grid and reads independently verified source cells directly. Fields retain original text and actual PDF glyph rectangles. Header and totals Groq requests use one compact call rather than issuing a request for every six mostly absent optional paths. Model host connectivity probes are disabled using the installed PaddleX flag.

PDF baseline spaces and punctuation are kept on their source lines without steering their ink baseline. Compact Groq evidence retains coordinates and explicitly distinguishes party information from banking details. Explicit Balance labels can identify a unique nearby printed amount on the same row. Amount/date normalization retains raw evidence and ambiguous dates. Groq batch aggregation accepts both supported values formats and preserves consistent table metadata.

Finalization marks jobs failed when document extraction fails. The original job's misleading historical failure status was corrected before a guarded retry. The user explicitly approved sending this invoice to Groq after automatic approval review required document/destination-specific consent.

Final verification uses a new same-tenant job so prior machine results are retained. Job job_aa691e42eb57440aa8a0bd009f627880 completed in 54.03 seconds with 17 saved fields, one complete cargo/line-item row, ten source-checked core values matching, and no buyer country inferred from the bank. Its status remains review_required due to the completeness check and a totals-stage provider connection failure. No worker restart occurred. Broad accuracy and production readiness are not established by this one document.

108 focused unit tests and 12 isolated PostgreSQL worker-failure integration tests passed. Lint and whitespace checks passed. An isolated full unit run found three broader configuration/classification test failures, outside the changed extraction path.

## Review Triage Log

Independent review found no new blocker in header mapping, digital source-cell validation, source line grouping, Balance association, Groq dict/table aggregation, phase-call changes, or failed-job finalization. Missing populated-phase coverage was addressed with real invoice runs and explicit comparison of ten recovered source values. Mixed-success finalization coverage remains a broader test gap.
