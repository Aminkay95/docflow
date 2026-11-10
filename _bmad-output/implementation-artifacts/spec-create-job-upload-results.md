---
title: 'Create job upload and extraction results'
type: 'feature'
created: '2026-11-05'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Complete the existing Create job flow: upload documents, submit them through the existing ingestion and worker pipeline, and open the job page showing processing status, detected document types and extracted field/value pairs. Build on the existing uncommitted implementation as authorized by the user. Keep session authentication, CSRF protection, upload limits and auditor read-only permissions.

</frozen-after-approval>

## Implementation Notes

- Investigation: routes.py already implements upload ingestion and job result rendering; templates/new_job.html, job.html and base.html already provide upload/results/navigation. review.js incorrectly tests the truthiness of empty data-upload, preventing interception and navigation. Fix attribute detection and hide creation controls from auditors. No intent gaps, migrations, deployment, or external side effects are needed.
- Reuse existing ingestion and pipeline integration test. Add focused browser-script regression coverage and upload authorization/validation checks. Preserve unrelated extraction/config changes.
- Implemented empty-attribute detection for upload interception, auditor controls, retry idempotency through the existing ingestion contract, inactive-client messaging, extension field rendering, validation before default-client creation, and recovery from concurrent internal-client creation.
- Verification: existing review integration suite had 15 passes and one incorrect new expected status (corrected from 422 to the actual 415 contract); subsequent focused integration run passed all six upload/summary tests. Three executable Node script regression tests pass; Ruff lint passes. Rebuilt local API image and refreshed the API service.
- No commit created: the user's index already contains unrelated staged extraction/configuration work, and the upload implementation itself was already mixed into their working changes.

## Review Triage Log

- Medium, patched: response-loss retries could duplicate jobs; preserve a submission key until inputs change and pass it through ingestion's idempotency support. Integration retry test verifies identical job and result IDs.
- Medium, patched: concurrent initial uploads could collide on internal-client slug; recover the winning active tenant after rollback.
- Low, patched in scope: invalid uploads could create an internal tenant; defer tenant creation until upload validation succeeds. A valid upload that later fails ingestion can still leave the dedicated tenant, which is reusable workspace setup rather than a lost job.
- Medium, patched: inactive-only tenants rendered misleading bootstrap text; show activation guidance and disable upload.
- Medium, patched: summary omitted extension fields; merge core and extension values consistently with the document workspace and verify field value/page output.
- False for final change: Python-only tests miss the JavaScript guard defect; added executable Node regressions for the empty data-upload attribute, CSRF, multipart submission, navigation, errors and missing selection.
