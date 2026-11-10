---
title: 'Inline editable job field values'
type: 'feature'
created: '2026-11-05'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="User requested inline field names and editable values">

## Intent

Make job-review fields compact rows with the field name and value side by side. Clicking an eligible value edits it directly, without opening a separate correction disclosure. Reuse existing correction save and reason requirements, source-page highlighting, dirty-edit protection and review authorization. Reviewed fields and auditors retain existing read-only behavior. Keep source preview and bulk/Done actions.

</frozen-after-approval>

## Implementation Notes

Small template/CSS change with no new backend contracts, migrations or intent gaps. Use an ordinary labeled value input within the existing data-resolve correction form. Reveal reason/save controls when the form has focus. Preserve provenance and raw field path tooltips. Verify actual browser interaction, compact desktop/mobile layout and current JavaScript regressions. Build on existing changes; preserve user's index and leave interleaved work uncommitted.

Implemented compact side-by-side names and native value inputs. Reason/save appear on focus; reviewed and auditor rows retain read-only values, correction overlays and original-value disclosure. Existing submission, source highlight and dirty-form behavior reused without JavaScript/backend changes.

Verification: 2 focused review/upload integration tests passed; 10 Node regressions and Ruff passed. Real Chromium desktop/mobile test passed direct value clicking, reason reveal, correction save, bulk confirmation, document selection and expired media handling. Screenshots visually inspected. Local API rebuilt/refreshed and healthy. Existing staged work preserved; no commit made.

## Review Triage Log

- Independent focused review found no confirmed new functional defects. Native focus remains within the form while moving to reason/save, and existing escaping, submission hooks and security conditions remain intact.
- Low, rejected optional enhancement: reason/save hide after focus leaves an unsaved form. Dirty-form tracking already prevents silent loss and controls reappear when the value is focused; retaining controls globally would add UI clutter beyond this requested layout.
