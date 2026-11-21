---
title: 'Client onboarding and management in the review console'
type: 'feature'
created: '2026-11-21'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 02e18bc92c4885add120b3ffec643e2aaa73c63b
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Operators can list clients but must use a setup script to onboard them. The console lacks the controls needed to issue credentials, configure delivery, and manage client access.

**Approach:** Extend the existing Clients area into an admin-operated onboarding and management flow. Admins create a client and its initial credentials, then manage its details, API keys, webhook configuration, and account status from a client detail screen.

## Boundaries & Constraints

**Always:** Reuse existing models, signed sessions, CSRF, hashing, encryption, URL validation and console styles. Restrict mutations and credentials to admins; check client/key ownership. Display secrets only in successful mutation responses with no-store caching; persist only hashes/encrypted secrets. Share provisioning with the setup script. Preserve nonsecret input on validation errors. Suspension and termination immediately block API authentication.

**Never:** Add public registration, billing, invitations, a customer portal, or database migrations. Delete client records or extraction history. Change unrelated extraction work. Put plaintext credentials in URLs, logs, cookies, storage, or subsequent detail-page responses. Claim existing review Activity is a client-management audit trail.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Onboard | Admin provides name, unique slug, optional webhook | Active client, initial scoped key and encrypted webhook secret created atomically; credentials displayed once | Invalid fields rejected without partial account |
| Duplicate slug | Existing slug or concurrent duplicate | Existing account unchanged | Readable conflict, no traceback |
| Key lifecycle | Admin selects client and creates named key or revokes owned key | New key shown once; revoked key denied immediately | Reject unknown scopes, invalid expiry, wrong-client key |
| Webhook | Admin sets URL/enabled state or explicitly rotates secret | Valid config saved; rotation displays replacement secret once | Reuse existing URL rules; enabled requires URL |
| Account access | Admin suspends, activates, or terminates client | Status and termination timestamp remain consistent; terminal accounts cannot be reactivated | Explicit confirmation for termination; reject invalid transitions |
| Unauthorized mutation | Reviewer, auditor, anonymous caller, or missing CSRF | No account or credential change | Existing authentication/CSRF errors; role denial for nonadmins |
| Refresh after credentials | Reload detail page after creation/rotation | Metadata and key prefixes only | Secret never redisplayed |

</frozen-after-approval>

## Code Map

- `src/trade_extract/review/routes.py`: extend `clients`; reuse `render`, `paginate`, `counts_for` and existing JSON response conventions.
- `src/trade_extract/review/auth.py`: `principal` checks sessions, CSRF and auditor writes; add an explicit admin dependency.
- `src/trade_extract/review/templates/clients.html`, `base.html`, `static/review.js`, `static/review.css`: existing shell, navigation, fetch errors and styles.
- `scripts/create_client.py`: credential format and provisioning contract to share with the console.
- `src/trade_extract/db/models/client.py`, `api_key.py`: existing status, termination, retention, scopes and lifecycle fields.
- `src/trade_extract/domain/hashing.py`, `services/webhooks.py`, `services/limits.py`: hashing, encryption and `validate_webhook_url`.
- `src/trade_extract/api/deps.py`: authentication already rejects inactive clients and revoked/expired keys.
- `tests/integration/test_review_trust.py`, `conftest.py`: login, CSRF and PostgreSQL harness. `test_scripts.py` covers script onboarding.
- `docs/05-ui-architecture.md`: admin controls and authorization requirements.

## Tasks & Acceptance

**Execution:**
- [x] `src/trade_extract/services/clients.py` -- share validated provisioning, key, webhook and status operations, with atomic writes.
- [x] `scripts/create_client.py` -- delegate provisioning while preserving arguments, output and exit behavior.
- [x] `src/trade_extract/review/auth.py`, `routes.py` -- add admin authorization and scoped management endpoints.
- [x] `src/trade_extract/review/templates/clients.html`, `client.html`, `new_client.html` -- add onboarding, detail, key and account controls.
- [x] `src/trade_extract/review/static/review.js`, `review.css` -- connect forms, validation, confirmation and transient secrets using existing conventions.
- [x] `tests/unit/test_clients.py`, `tests/integration/test_client_management.py` -- test matrix edge cases, atomicity, roles/CSRF, ownership and actual API access effects.
- [x] `README.md` -- document console onboarding and one-time credentials.

**Acceptance Criteria:**
- Given an admin, when opening Clients, then onboarding and management are discoverable without a script.
- Given a new client, when its key submits a valid document, then the API accepts it under that client.
- Given a nonadmin, when opening Clients, then metadata remains accessible while credential controls and writes are denied.
- Given existing extraction and review flows, when management is added, then console and script regressions still pass.

## Implementation Notes

- Shared provisioning and admin-only management are complete; existing CLI output remains compatible. No migrations were required.
- Review corrections preserve unsaved one-time credentials, prevent confirmation reentry, restore cached onboarding to canonical detail, clarify default webhook scope, and label expired keys.
- Visual inspection covered list, onboarding, client detail including webhook/status controls, validation and dummy one-time credentials at 390px and 1440px. Positioned the list table container to contain its hidden heading; document overflow is eliminated while tables scroll within their cards.
- Verification: 42 PostgreSQL management/script/review regression tests passed before final review patches. Final focused management/script/unit/template run: 29 passed. Focused browser suite: 12 passed. Lint and whitespace checks passed.
- Matrix audit: onboarding/refresh covered by onboarding credentials test; duplicate/atomicity by validation conflicts and commit-failure tests; key ownership/scopes/expiry/revocation by lifecycle and expiry tests; webhook/status by lifecycle; roles/CSRF/anonymous by authorization tests. Every covering case ran and passed.
- Baseline comparison: existing review_upload browser suite fails 9/20 tests on both baseline 02e18bc and current code. Deferred as pre-existing; new client browser cases pass.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: credential replacement | medium | A second key/rotation response clears the currently displayed credentials. Patch: require explicit continuation before another credential mutation. |
| Blind 2: key table refresh | false | The successful response intentionally pauses at one-time credentials; Continue loads the detail page and its current key table, including revocation. No lost management path. |
| Blind 3: cached onboarding restore | medium | pagehide clears secrets while the creation form stays hidden. Patch: persisted pageshow returns to canonical detail URL. |
| Blind 4: confirmation reentry | medium | The submit guard is acquired only after asynchronous confirmation; two listeners can submit the confirmed action. Patch: acquire/release guard around confirmation. |
| Blind 5: webhook disable scope | medium | Delivery creation uses per-submission override URLs and queued deliveries retain their URLs. Patch: explain that this setting controls default delivery and does not cancel queued deliveries or explicit submission overrides. |
| Blind 6: form conversions | medium | Existing JS tests cover only creation and do not inspect posted payloads. Patch: exercise each management form conversion. |
| Blind 7: scope permissions | medium | The new key path has no denied-extraction or stored exact-scope assertion. Patch: add both to its lifecycle test. Account-write API does not exist, so no such request can be tested. |
| Blind 8: commit failure | medium | Rollback is tested before commit only. Patch: force commit failure and assert no disclosed credentials or durable account/key. |
| Blind 9: expired label | low | Access column shows no expiry status though timestamp exists. Direct patch: label expired keys using application clock. |
| Verification 1: scoped grant | medium | Pre-verified gap in the same lifecycle test as Blind 7. Patch grouped with Blind 7. |
| Verification 2: form payloads | medium | Pre-verified gap in browser conversion coverage. Patch grouped with Blind 6. |
| Parent: mobile table overflow | low | Hidden absolute heading escaped its scroll container; position the client-list table container to contain it. Verified at 390px. Direct patch completed. |
| Baseline JS regressions | medium | Nine identical failing legacy workspace tests reproduced against baseline and current script; defer as pre-existing. |

## Verification

**Commands:**
- `.venv/Scripts/python.exe -m pytest tests/unit/test_clients.py -q` -- all service validation tests pass.
- `.venv/Scripts/python.exe -m pytest tests/integration/test_client_management.py tests/integration/test_scripts.py tests/integration/test_review_trust.py -q` -- PostgreSQL-backed authorization, credential, onboarding and console regressions pass.
- `.venv/Scripts/python.exe -m ruff check src/trade_extract/services/clients.py src/trade_extract/review scripts/create_client.py tests/unit/test_clients.py tests/integration/test_client_management.py` -- changed code passes lint.

Inspect rendered list, onboarding, detail, validation and one-time credential views at desktop and narrow widths; verify secrets disappear after refresh.
