---
title: 'Job review and terminal JSON handoff'
type: 'feature'
created: '2026-11-05'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent">

## Intent

Print completed pipeline JSON in the worker terminal. Extend the existing job details page with review progress, JSON preview and a Done / Push result action. Once processing and required reviews finish, the action prints the reviewed JSON in the API terminal and returns the same payload to the browser. The user selected terminal delivery now and client ERP delivery later. Preserve original extraction evidence and expose reviewed values separately. Keep authenticated, CSRF-protected write access and read-only auditor permissions. No ERP delivery or new database state is required for this terminal-only handoff.

</frozen-after-approval>

## Implementation Notes

- Small additive changes reuse the existing job page, read_document/build_result, review counts and existing worker commit boundary. No intent gaps, migrations or irreversible external actions. Use a shared job-result builder for preview and handoff; keep original values/corrections and add effective fields with corrections applied.
- Pipeline terminal output must occur after successful commit and include a phase marker to distinguish automatic extraction output from manually reviewed handoff. Guard Done server-side against active submissions, pending documents and open reviews/conflicts, using the same job lock as review/ingestion.
- Tests cover post-commit output, corrected values, incomplete review rejection, completed handoff, authentication and browser action handling. Preserve unrelated work and the user's existing index.
- Implemented shared job_result/emit_terminal_result, job-wide effective fields, retained review resolutions including chosen conflict sources, JSON preview and guarded POST Done action. Automatic pipeline output emits after successful finalization or permanent failure commits. Manual reviewed output returns the same JSON shown in the browser. Terminal phases are pipeline.result and review.result.
- Verification: 21 review integration tests passed; subsequent focused runs verify corrected handoff and permanently failed pipeline terminal output. Five Node regressions passed, Ruff passed, mypy passed for three changed modules. Rebuilt and refreshed local API/worker; health and authenticated handoff smoke checks passed.
- User's pre-existing staged and unstaged work preserved; no commit made because implementation is mixed with existing changes and staged extraction work.

## Review Triage Log

- Medium, patched: accepted conflict sources were absent from handoff; export review_resolutions with chosen_document_id and keep source values intact. Added accepted/waived conflict export assertions.
- Medium, patched: review metadata was omitted; include reviewer ID, reason, timestamp, outcome and corrected values, and verify correction metadata.
- Medium, patched: permanent task failures could end without terminal output; emit after failure transaction commits, only when submission actually reaches failed state. Regression verifies one failed result and no retry-attempt output.
- Low, patched: terminal write failure could be silent except for a warning; surface terminal_result_failed at error level with the job details recovery URL. JSON remains available in the page preview. Durable ERP delivery is explicitly deferred by user scope.
- Medium verification gap, patched: added accepted/waived conflict and permanent worker failure coverage alongside corrected-field and browser action tests. Multi-submission and extension paths reuse the job-wide existing read_document contract.
