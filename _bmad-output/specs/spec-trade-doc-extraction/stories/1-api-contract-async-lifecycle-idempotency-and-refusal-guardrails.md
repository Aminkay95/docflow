---
title: 'API contract, async lifecycle, idempotency and refusal guardrails'
type: 'feature'
created: '2026-11-01'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
baseline_revision: '2ca59784cc08bd36c35a45455284ef334e3c7370'
context:
  - 'docs/02-file-structure.md'
  - 'docs/03-database-schema.md'
  - 'docs/04-api.md'
  - 'docs/openapi.yaml'
warnings: ['oversized']
deferred:
  - summary: >-
      `.env.example` omits several optional settings that operators can tune.
    evidence: |-
      `JOB_MAX_ATTEMPTS`, `MAX_JOB_REF_LENGTH`, `MIN_IDEMPOTENCY_KEY_LENGTH`,
      `MAX_IDEMPOTENCY_KEY_LENGTH`, `MAX_WEBHOOK_URL_LENGTH` and
      `MIGRATION_TEST_DATABASE_URL` are read but not documented; every one has a
      validated default, so no behaviour is wrong, but an operator cannot discover them.
    location: >-
      .env.example
    severity: low
  - summary: >-
      AWS credentials supplied through a `.env` file never reach the boto3 client.
    evidence: |-
      `Settings` uses `extra="ignore"` and does not export `env_file` values into
      `os.environ`, while `S3Storage` builds a plain `boto3.client("s3")`. Production
      injects real environment variables, which boto3 reads, so only a `.env`-file S3
      development setup is affected.
    location: >-
      src/trade_extract/services/storage.py:115
    severity: low
  - summary: >-
      Every error `doc_url` points at an example host that serves no catalogue.
    evidence: |-
      `domain/errors.py` and `config.docs_base_url` both hard-code
      `https://docs.trade-extract.example/errors`; the error catalogue is a
      documentation deliverable with no host in this story.
    location: >-
      src/trade_extract/config.py:112
    severity: low
  - summary: >-
      Published OpenAPI `servers` are hard-coded instead of deriving from `base_url`.
    evidence: |-
      `api/openapi.py` lists `https://api.trade-extract.example` and
      `http://localhost:8000` although `settings.base_url` is what produces
      `status_url` and `result_url`; cosmetic metadata on the published schema.
    location: >-
      src/trade_extract/api/openapi.py:30
    severity: low
  - summary: >-
      Generated OpenAPI does not declare the response headers the polling contract uses.
    evidence: |-
      `Location`/`Retry-After` on the 202 and `Retry-After`/`ETag`/`Cache-Control`
      and `X-Request-Id` on results are emitted and asserted at HTTP level but absent
      from the generated schema, so a generated client cannot discover them.
    location: >-
      src/trade_extract/api/openapi.py
    severity: low
  - summary: >-
      The `Document` response model allows extra fields, so the contract test cannot
      see new response members.
    evidence: |-
      `ConfigDict(extra="allow")` publishes `additionalProperties: true`; a future
      field could appear in responses without failing the two-direction contract check.
    location: >-
      src/trade_extract/api/schemas/results.py
    severity: low
  - summary: >-
      Webhook delivery handling ignores some transient signals and a redirect status.
    evidence: |-
      A 429 is retried on the fixed schedule without honouring its `Retry-After`, and a
      3xx is neither success nor terminal so a permanently redirecting endpoint spends
      the whole attempt budget; redirect refusal itself is deliberate SSRF safety.
    location: >-
      src/trade_extract/services/webhooks.py:377
    severity: low
  - summary: >-
      The migration drift guard cannot compare the Postgres-specific objects this
      schema depends on.
    evidence: |-
      `alembic check` does not compare CHECK constraints, partial or expression
      indexes, the `segments` exclusion constraint or triggers, so models and the frozen
      DDL could diverge on exactly those constructs. The migration test asserts them
      directly against PostgreSQL, which mitigates but does not close the gap.
    location: >-
      tests/integration/test_migrations.py
    severity: low
  - summary: >-
      `create_client` grants scopes no route checks and can race its slug uniqueness.
    evidence: |-
      `DEFAULT_SCOPES` includes `account:read`/`account:write` that nothing requires,
      and the slug check is read-then-write, so a concurrent create surfaces as an
      `IntegrityError` traceback rather than the script's usual remediation message.
    location: >-
      scripts/create_client.py
    severity: low
  - summary: >-
      Integration fixtures are destructive with no test-database guard.
    evidence: |-
      `db_engine` runs `alembic downgrade base` and `clean_database` truncates every
      table against whatever `TEST_DATABASE_URL` resolves to; a mis-set variable would
      drop a real schema. The suite is documented as requiring a dedicated database.
    location: >-
      tests/integration/conftest.py
    severity: low
  - summary: >-
      Queue and request-path concurrency tuning is unaddressed.
    evidence: |-
      `requeue_stale` uses a legacy query without `FOR UPDATE SKIP LOCKED`, so two
      maintenance replicas can both refund attempts; `active_count_for_job`
      materialises rows in Python; `get_api_key` writes `last_used_at` on every
      request. The documented deploy runs a single maintenance process.
    location: >-
      src/trade_extract/services/queue.py
    severity: low
  - summary: >-
      Compose health and restart policy are uneven.
    evidence: |-
      The API healthcheck polls liveness rather than readiness, so a container with no
      database or policy reports healthy; worker and maintenance have no healthcheck and
      no restart policy. This is development-compose polish.
    location: >-
      docker-compose.yml:67
    severity: low
  - summary: >-
      Cross-rule precedence is not pinned when one streamed request breaks two rules.
    evidence: |-
      The intent fixes the validation order but not which of two simultaneously broken
      rules wins inside one request; a test would enshrine an implementation choice
      rather than a documented requirement.
    location: >-
      tests/integration/test_extract_api.py
    severity: low
---

<intent-contract>

## Intent

**Problem:** The repository has a validated design but no running service. Every later story needs a stable intake contract: callers must submit a grouped set of files, get a result id, and learn the outcome by polling or webhook, while bad input is refused before anything is persisted.

**Approach:** Scaffold the Python/FastAPI/PostgreSQL stack and implement the Phase 1 slice: API-key auth, streamed `POST /v1/extract` validation, job/submission/file persistence, a durable queue with a stub completion pipeline, `GET /v1/results/{result_id}`, and signed webhook delivery. One error envelope, and every refusal names the rule broken and the remediation.

## Boundaries & Constraints

**Always:**
- Validation order is fixed and runs before storage: auth/scope → group count 1–20 → per-file ≤ 26,214,400 bytes while streaming → magic-byte type (PDF/JPEG/PNG/WebP/TIFF) → container opens (encrypted/malformed/zero pages) → 500 pages/file and 2,000 pages/submission → multi-shipment guard. Any failure leaves no job, submission, file row or stored object.
- Whole files may spool to a temp file for inspection; temp files are removed on every exit path. Object storage writes happen only after every file passes.
- One error envelope: `{"error":{"code","message","request_id","details","doc_url"}}`. `message` names the broken rule **and** the remediation; `details` carries the machine-readable evidence (file, limit, actual, counts).
- Per-document idempotency key = `sha256(source_file_sha256 + ":" + job_ref + ":" + page_start + ":" + page_end + ":" + document_type)`, unique per `(job_id, key)`. File content is unique per `(job_id, sha256)`.
- `job_ref` is 1–128 printable ASCII characters; `Idempotency-Key` is 8–128 printable characters; `webhook_url` is ≤2,048 characters and must be HTTPS outside development. Violations are `400 invalid_request` with the offending field and its rule.
- Submission idempotency: same `Idempotency-Key` + same request fingerprint returns the original `202` body and creates nothing; same key + different fingerprint returns `409 idempotency_key_reused`.
- `job_ref` accumulation: same client + non-null `job_ref` upserts one job; an omitted `job_ref` always creates a new job that cannot accumulate; a completed job reopens as `queued` with `terminal_at` cleared; a failed job returns `409 job_failed`.
- Multi-shipment guard uses positive evidence only: ≥2 distinct normalized bill-of-lading numbers, each anchored to an explicit B/L label in the embedded text layer, rejects with `422 multi_shipment_container` carrying `shipment_count`, the numbers, and a split instruction. No text layer, no label, or an unparseable token is ambiguity and proceeds.
- Webhook payloads carry ids, counts and a `result_url` only — never document content — and are signed `HMAC-SHA256(secret, timestamp + "." + raw_body)`.
- Limits are configuration resolved at the process boundary, not literals in handlers.

**Never:**
- Never store or partially accept a file that fails any earlier check; no "store then clean up" path.
- Never trust a declared `Content-Type` or filename for type, grouping or document type.
- Never create document rows from application code here: documents are segment outputs (story 2). This story completes envelope-only results.
- Never regroup, match or infer membership beyond the caller's `job_ref`.
- Never import `paddleocr`, `cv2`, `numpy` or any reader into the API package.
- Out of scope: extraction, OCR, segmentation, checks, conflicts, review UI, usage/rate limiting, exports and retention sweeper.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| HAPPY_PATH | 3 valid PDFs, `job_ref=BL-1`, valid key | `202` with `result_id`, `job_id`, `status=queued`, `submitted_documents=3`; rows + `prepare_file` tasks created; files in storage | — |
| IMAGE_ONLY | One JPEG (no text layer) | `202`; guard proceeds (no evidence) | — |
| ZERO_FILES | No `documents` parts | Rejection, nothing stored | `422 no_documents`; message says a group needs 1–20 files |
| GROUP_TOO_BIG | 21 file parts | Rejection before reading part 21, nothing stored | `422 too_many_documents`; details `{limit:20, actual:21}`; message says split into groups of ≤20 |
| FILE_TOO_BIG | One file of 26,214,401 bytes | Rejection mid-stream, nothing stored | `413 file_too_large`; details `{document,limit_bytes,actual_bytes}`; message says compress/downscale below 25 MB |
| UNSUPPORTED_TYPE | DOCX/ZIP/macro file | Rejection before storage | `415 unsupported_media_type`; message lists accepted types |
| ENCRYPTED | Password-protected PDF | Rejection before storage | `422 encrypted_document`; message says remove the password and resubmit |
| MALFORMED | Truncated PDF, zero pages | Rejection before storage | `422 malformed_document`; message says re-export the file |
| PAGE_LIMIT | 501-page PDF / 2,001 pages total | Rejection before storage | `422 page_limit_exceeded`; details `{file_pages, submission_pages, limits}`; message says split the file/group |
| MULTI_SHIPMENT | Two distinct labelled B/L numbers across the group | Rejection before storage | `422 multi_shipment_container`; details `{shipment_count,bill_of_lading_numbers}`; message says split and resubmit one shipment per `job_ref` |
| ONE_BL | One labelled B/L number referenced by invoice + B/L | `202` | — |
| DUPLICATE_FILE | Same bytes resubmitted under same `job_ref` | `202`, `duplicate_documents=1`, no second file or task; existing output counted | — |
| ALL_DUPLICATES | Every part already in the job | `202`; submission immediately `completed`, no tasks enqueued, `duplicate_documents` equals the part count | — |
| REPLAY | Same `Idempotency-Key` + identical parts | Original `202` body returned; no new submission/tasks | — |
| KEY_REUSED | Same `Idempotency-Key`, different parts/fingerprint | No new submission | `409 idempotency_key_reused`; message says use a new key |
| FAILED_JOB | `job_ref` names a failed job | No new submission | `409 job_failed`; message says use a new `job_ref` |
| REOPEN | New submission on a completed job | Job returns to `queued`, `terminal_at` cleared | — |
| POLL_UNKNOWN | Unknown or other tenant's result id | No body leakage | `404 result_not_found` |
| POLL_UNCHANGED | Poll with the `ETag` from a prior response | `304` with no body | — |
| BAD_WEBHOOK_URL | `webhook_url` of 2,048+ chars, non-HTTPS in production, or non-URL | Rejection before storage | `400 invalid_request`; message names the field and the HTTPS/length rule |
| WEBHOOK | Submission reaches completion with a webhook URL | Signed `result.completed` event POSTed; payload has ids/counts/`result_url` only | Failed delivery retries per backoff, then `dead` |
| AUTH | Missing/revoked key, or key missing `extract:write` on submit or `results:read` on poll | Rejection before body processing | `401 unauthorized` / `403 forbidden` |

</intent-contract>

## Code Map

- `docs/04-api.md` — canonical endpoint, validation-order, idempotency, error-envelope and webhook contract.
- `docs/03-database-schema.md` §5–§8 — DDL for `clients`, `api_keys`, `policies`, `jobs`, `submissions`, `files`, `job_tasks`, `webhook_deliveries`; job/submission state transitions.
- `docs/02-file-structure.md` §2–§3 — module responsibilities and import boundaries.
- `docs/openapi.yaml` — committed external contract; implemented routes must conform.
- `pyproject.toml`, `alembic.ini`, `migrations/` — new project packaging, migration harness and initial schema.
- `src/trade_extract/config.py` — env-only `Settings` incl. `max_documents_per_group`, `max_file_bytes`, page caps, `api_key_pepper`, `secret_key`, `storage_backend`, `base_url`, webhook settings.
- `src/trade_extract/domain/hashing.py` — `sha256_file`, `request_fingerprint`, `document_idempotency_key`, `stable_json`.
- `src/trade_extract/domain/errors.py` + `api/errors.py` — domain error codes and the single envelope mapper.
- `src/trade_extract/db/models/` — SQLAlchemy 2.0 models mirroring the DDL; `db/repositories/` — job upsert/reopen, submission create/replay, file insert, webhook outbox.
- `src/trade_extract/services/limits.py` — streamed multipart validation, magic-byte sniff, group/file/page caps, B/L evidence guard.
- `src/trade_extract/services/page_model.py` — `probe_container()`: page count and per-page embedded text only (lazy `pypdfium2` import), with the page cap passed in so text extraction stops before the cap is breached; `probe_image()` decodes JPEG/PNG/WebP/TIFF with Pillow, counts TIFF frames, and rejects truncated or malformed images. Extended by story 2.
- `src/trade_extract/services/storage.py` — `ObjectStorage` protocol + filesystem and S3 implementations, key scheme; the `s3` extra is installed in both images.
- `src/trade_extract/services/ingest.py` — validate-then-accept, temp spool cleanup, idempotent replay of the frozen acceptance body, duplicate counting, task fan-out, constraint-race handling.
- `src/trade_extract/services/queue.py` — `SKIP LOCKED` claim, retry/backoff, dead-letter, stale-task requeue; a dead task terminalises its file and submission.
- `src/trade_extract/services/webhooks.py` — outbox enqueue, HMAC signing, SSRF guard, configurable retry policy, stale `delivering` reclaim, `result.failed` events.
- `src/trade_extract/api/routes/{health,extract,results}.py` — thin adapters; `api/deps.py` — API-key auth/scope; `api/middleware.py` — request id and logging. The 202 body does not advertise a `job_url` until a job-read route exists.
- `src/trade_extract/workers/runner.py` + `handlers/{prepare_file,finalize_submission}.py` — stub pipeline that marks files processed and finalizes the submission, and terminalises failures; `workers/maintenance.py` — webhook delivery, stale-task requeue and stale-delivery reclaim loops, resilient to transient errors.
- `scripts/bootstrap.py`, `scripts/create_client.py` — seed the active policy and create client/API key/webhook secret out of band.
- `tests/unit`, `tests/integration/test_extract_api.py`, `tests/contract/test_openapi.py` — new test suites.

## Tasks & Acceptance

**Execution:**
- `pyproject.toml`, `.env.example`, `.gitignore`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_initial.py` — package the project, pin Python 3.12, put `pypdfium2` and Pillow in base dependencies, put OCR packages only in extras, install a `s3` extra, ignore `var/`, and write the initial schema as **literal forward-only DDL** (never `Base.metadata.create_all` inside a migration), including `btree_gist`, `set_updated_at()` triggers and the `segments` exclusion constraint.
- `tests/integration/test_migrations.py` — upgrade an empty scratch database with Alembic, assert the extension, trigger function, six triggers and `ex_segments_page_range` exist, then downgrade to base; integration fixtures use the migrated schema, not ORM metadata.
- `Dockerfile.api`, `Dockerfile.worker`, `docker-compose.yml` — api/migrate/worker/postgres services, healthchecks, named volume; both images install `--extra s3`; compose passes the webhook and pool settings from `.env.example`; API image stays OCR-free.
- `src/trade_extract/config.py`, `src/trade_extract/logging.py` — environment-only settings with validated limits (including `max_request_bytes` = 20 × per-file cap plus multipart overhead, and `retry_after_seconds`), reject example-placeholder secrets in production, and structured JSON logging that never logs content.
- `src/trade_extract/domain/{ids,enums,clock,hashing,errors}.py` — prefixed IDs, state enums, injectable clock, the three hashes above, typed domain errors including `client_inactive` and `payload_too_large`; every message names the rule and a caller-actionable remedy.
- `src/trade_extract/db/{base,session}.py`, `db/models/*.py`, `db/repositories/*.py` — sync SQLAlchemy session; upsert/reopen job, create submission, insert file, enqueue task, webhook outbox; API-key lookup filters `revoked_at IS NULL`; shared key-hash helper.
- `src/trade_extract/services/page_model.py` — container probe returns page count and embedded text, stopping before text extraction once the per-file or submission page cap is exceeded; image probe decodes JPEG/PNG/WebP/TIFF with Pillow, counts TIFF frames, and maps truncated/unsupported image data to `malformed_document`.
- `src/trade_extract/services/limits.py` — ordered validators and B/L evidence extraction; enforces the whole-request byte cap for every part including ignored fields; zero-byte parts are `malformed_document` (size checked before type); an empty optional `webhook_url` is absent; webhook URLs reject embedded credentials and redact the value; B/L evidence takes only the first token immediately after a label.
- `src/trade_extract/services/storage.py` — protocol plus filesystem (atomic temp-then-replace, path escape rejection) and S3 adapters.
- `src/trade_extract/services/ingest.py` — accept after validation: job upsert, submission/file rows, storage puts with compensating delete, `prepare_file` tasks, duplicate handling, exact-202 replay with the frozen acceptance snapshot (`status: queued`, no `job_url`); constraint races retry the job upsert, count duplicate-file races as duplicates, and reserve `idempotency_key_reused` for the submission-key constraint.
- `src/trade_extract/services/queue.py` — claim/mark succeeded/failed/dead with configurable exponential backoff; task payloads hold ids only; `requeue_stale` for crashed claims; dead tasks terminalise their file and submission.
- `src/trade_extract/services/webhooks.py` — enqueue in the completion transaction, sign, resolve/block SSRF targets, configurable retry policy (`min(3, max_attempts)` for other 4xx), reclaim stale `delivering` rows, and emit `result.failed`.
- `src/trade_extract/api/{app,errors,middleware,deps}.py` — app factory, error envelope, request id, bearer-key auth with HMAC pepper + scopes; suspended/terminated clients get a correct 403 message; unknown paths return an envelope whose code names the missing endpoint rule.
- `src/trade_extract/api/routes/{health,extract,results}.py`, `api/schemas/*.py` — the three routes, multipart parsing, 202 shape, result polling with `no-store`/`ETag`/list-aware `If-None-Match`/`Retry-After`; validate `include` against the documented enum and apply `document_id` to the submission's documents; set `policy.effective_at`; OpenAPI bounds derive from settings.
- `src/trade_extract/workers/runner.py`, `workers/handlers/{prepare_file,finalize_submission}.py`, `workers/maintenance.py` — idempotent stub lifecycle; a finalize task re-arms while siblings are active and never overwrites a failed submission; dead tasks mark the file and submission failed and enqueue `result.failed`; maintenance catches transient errors, requeues stale tasks and reclaims stale deliveries.
- `scripts/bootstrap.py`, `scripts/create_client.py` — active policy v1 and developer client/key creation.
- `tests/unit/test_hashing.py`, `test_limits.py`, `test_webhook_signature.py` — key formula, every validation rule, whole-request cap, zero-byte/empty-optional cases, B/L negative cases (dates, seals, invoice numbers, container numbers), signature vector.
- `tests/integration/test_extract_api.py`, `test_lifecycle.py`, `test_webhooks.py`, `test_worker_failures.py`, `test_images.py`, `test_health.py` — end-to-end 202→queue→stub completion→poll/webhook; every rejection asserting no DB rows and no stored objects; replay after completion; dead-task failure terminalisation and `result.failed`; stale-task requeue; truncated JPEG and multi-page TIFF; readiness 200/503; expired key and inactive client.
- `tests/contract/test_openapi.py`, `test_error_envelope.py`, `test_api_imports.py` — generated schema conforms to `docs/openapi.yaml` in both directions for implemented routes; every error has the stable envelope; API import boundary holds.

**Acceptance Criteria:**
- Given a valid key and 1–20 valid files, when `POST /v1/extract` is called, then it returns `202` and the result id is pollable to a terminal completed state and, when a webhook URL is configured, a signed `result.completed` event is delivered with the same `result_id`.
- Given any rejected submission, when the response is inspected, then `error.code` is stable, `error.message` names the rule and the caller's remediation, and the database has no new job/submission/file/task row and storage holds no object or temp file.
- Given the same `Idempotency-Key` and identical parts, when the request is repeated after the submission has completed, then the response is the original `202` body with `status: queued` and no second submission, file or task exists.
- Given the same bytes submitted twice under one `job_ref`, when the second call completes, then `duplicate_documents` counts the repeat and only one file row and one task set exist.
- Given two distinct labelled B/L numbers in the embedded text, when a submission is attempted, then it is rejected `422 multi_shipment_container` naming `shipment_count` and instructing the caller to split per `job_ref`; given a B/L label whose line also carries a date, seal or invoice number, the extra token is not evidence.
- Given an image container that is truncated or whose frames cannot be decoded, when it is submitted, then it is rejected `422 malformed_document` before storage; a multi-page TIFF contributes its real frame count to the page caps.
- Given a task that exhausts its retries, when the worker marks it dead, then its file and submission become `failed`, polling returns a failed result with a reason, and a `result.failed` webhook is enqueued when configured; a stale `running` task is requeued by maintenance.
- Given the same file bytes, `job_ref`, page range and document type, when the per-document idempotency key is computed again, then it is byte-identical, and a direct insert of two document rows with the same `(job_id, idempotency_key)` is rejected by the schema (application code creates no document rows in this story).
- Given a failed job, when further documents arrive under its `job_ref`, then the call returns `409 job_failed` and creates nothing; given a completed job, a new submission reopens it as `queued` with `terminal_at` null.

## Spec Change Log

### 2026-11-01 — Review pass 1 (bad_spec loopback)
- **Triggering findings:** the multi-shipment evidence rule false-rejected legitimate single shipments (dates/seals/invoice numbers on a B/L line); the stub lifecycle had no terminal failure path for dead tasks or stale claims; `0001_initial` built the schema from live metadata and no test executed the migration; image containers were never decoded, so truncated images entered storage; whole-request bytes and duplicate/job-ref races were unaccounted.
- **Amended:** Tasks & Acceptance (literal DDL and migration test, failure terminalisation and maintenance recovery, image decoding, whole-request cap, frozen 202 snapshot, constraint-race handling, result `include` validation, S3 extras, loop resilience, auth/health tests); Code Map (page probe stops at caps, failure/recovery services, no `job_url`); Design Notes (tightened B/L token rule, page-cap ordering, image validation, failure lifecycle, frozen acceptance body, whole-request accounting, concurrency, migration integrity, result scope); Verification (migration and worker-failure suites).
- **Known-bad state avoided:** false `422 multi_shipment_container` on a legitimate group; a dead task leaving a submission `processing` forever with no event; a deploy schema that no test exercises and that changes whenever models change; truncated images stored as valid; unbounded ignored multipart parts; a replay returning `completed` where the original `202` said `queued`; a race surfacing as a bare 500.
- **KEEP:** validate-then-store ordering and temp-spool cleanup; the single error envelope with rule-plus-remediation messages; 20-file and 25 MiB early streaming checks; magic-byte typing; lazy `pypdfium2` import and the OCR-free API image; job upsert/reopen and per-document idempotency formula; duplicate counting; signed webhook outbox with SSRF guard and retry schedule; health routes; the import-boundary and OpenAPI contract tests.

## Review Triage Log

### 2026-11-01 — Review pass
- verdicts: 53 findings — high 12, medium 23, low 17, false 1, maybe-false 0
- findings:
  - `[medium]` `[bad_spec]` No whole-request cap; ignored parts stream unbounded — amended Specifications to add `max_request_bytes` counting every part and `413 payload_too_large`.
  - `[high]` `[bad_spec]` No failure path: files/submissions/jobs never become failed; `mark_failed` unused — amended failure lifecycle and worker tasks.
  - `[high]` `[bad_spec]` Dead task wedges submission; `finalize` dedupe never re-arms — amended dead-task terminalisation and active-only dedupe.
  - `[medium]` `[bad_spec]` Stale-task and stale-delivery recovery defined but never scheduled — amended maintenance loop requirements and tests.
  - `[low]` `[patch]` Failed files counted as completed in `progress` — amended counts; assert completed counts only `processed`.
  - `[medium]` `[patch]` `job_url` advertises an unimplemented endpoint — amended 202 body to omit `job_url` until a job-read route exists.
  - `[medium]` `[patch]` Inactive-client 403 recites a nonsense scope — amended dedicated `client_inactive` error naming account status.
  - `[medium]` `[bad_spec]` Every `IntegrityError` becomes `idempotency_key_reused`; job-ref race is 500 — amended concurrency handling.
  - `[high]` `[patch]` API/worker images omit the `s3` extra — amended Dockerfiles to install it.
  - `[high]` `[patch]` Production secret guard accepts `.env.example` placeholders — amended settings validation to reject placeholders.
  - `[high]` `[bad_spec]` `0001_initial` is live metadata, not frozen DDL; never executed by tests — amended literal DDL plus migration test.
  - `[low]` `[patch]` OpenAPI hardcodes limits; contract test checks one direction — amended settings-derived bounds and both-direction test.
  - `[medium]` `[patch]` Replay after completion returns live status instead of the original 202 body — amended frozen acceptance snapshot.
  - `[low]` `[patch]` Zero-byte part returns 415 and the empty-file branch is unreachable — amended size-before-type ordering.
  - `[medium]` `[bad_spec]` Images never decoded; multi-page TIFF counted as one page — amended Pillow decoding and frame counts.
  - `[high]` `[bad_spec]` B/L rule treats any numeric token on a label line as evidence; test used a literal backslash-n — amended first-token-after-label rule and negative cases.
  - `[low]` `[patch]` `include`/`document_id` accepted then discarded; ETag list parsing — amended enum validation and list-aware comparison.
  - `[low]` `[patch]` Result counts mix job/submission scope; `policy.effective_at` always null — amended scope note and effective_at.
  - `[low]` `[defer]` Dead/unreachable surface and duplicate state enum sources — cleanup belongs to the story that wires review/queue states.
  - `[low]` `[patch]` API-key lookup can return a revoked row; key hash duplicated — amended revoked filter and shared helper.
  - `[high]` `[patch]` Webhook URL accepts embedded credentials and echoes raw value — amended userinfo rejection and redaction.
  - `[false]` `[reject]` `Retry-After: "2"` violates the configuration rule — the rule covers limits, not a documented polling hint; no user harm.
  - `[low]` `[reject]` Story doc lacks change/triage entries and matrix rows — expected pre-review state; its fix is to edit the spec, and the review log now covers the gaps.
  - `[low]` `[reject]` Docs validation order and message example differ — the spec fixes the order; docs are context, and the difference is developer-only prose.
  - `[low]` `[defer]` README/compose operational documentation gaps — non-correctness follow-up.
  - `[high]` `[patch]` (grouped: S3 image extra) — same as Dockerfile finding above.
  - `[medium]` `[patch]` (grouped: inactive-client message) — same as auth message finding above.
  - `[low]` `[patch]` (grouped: ETag/include) — same as result-filter finding above.
  - `[low]` `[defer]` New submission on `review_required` job keeps status — unreachable in story 1 (no review items); later review story owns it.
  - `[medium]` `[patch]` (grouped: replay snapshot) — same as replay finding above.
  - `[medium]` `[defer]` Crash between `storage.put` and commit leaves an orphaned object — blueprint prescribes put-before-commit with compensation; retention sweeper owns orphan cleanup.
  - `[medium]` `[bad_spec]` Resubmitting bytes after a failed task counts as a duplicate and never retries — amended failure lifecycle to treat failed/purged files as new work.
  - `[medium]` `[defer]` Job row lock held across object-storage writes — blueprint's documented ingest pattern; concurrency tuning is later.
  - `[medium]` `[bad_spec]` (grouped: constraint races) — same as `IntegrityError` mapping finding above.
  - `[low]` `[patch]` (grouped: zero-byte) — same as empty-file ordering finding above.
  - `[medium]` `[patch]` Empty optional `webhook_url` is rejected as invalid — amended empty value treated as absent.
  - `[medium]` `[patch]` Page caps parsed and text-extracted before rejection — amended probe to stop at the cap.
  - `[medium]` `[bad_spec]` (grouped: stale recovery) — same as stale-task finding above.
  - `[medium]` `[defer]` SSRF DNS-rebinding window between resolve and connect — blueprint's documented guard; pinning the resolved address is a later hardening change.
  - `[low]` `[patch]` Other-4xx dead-letter threshold ignores `WEBHOOK_MAX_ATTEMPTS < 3` — amended `min(3, max_attempts)`.
  - `[low]` `[bad_spec]` Finalize can overwrite a failed submission and webhook it as success — amended finalize idempotency for failed status.
  - `[high]` `[bad_spec]` (grouped: dead-task wedge) — same as dead-task finding above.
  - `[medium]` `[patch]` Maintenance loop exits on any transient error — amended loop resilience.
  - `[high]` `[bad_spec]` (grouped: no runner failure terminalisation) — same as failure lifecycle finding above.
  - `[medium]` `[patch]` (grouped: replay after completion) — same as replay finding above.
  - `[high]` `[bad_spec]` (grouped: migration never executed) — same as migration finding above.
  - `[medium]` `[patch]` Retry→dead transition has no test — amended worker-failure test task.
  - `[medium]` `[patch]` Expired-key and inactive-client branches untested — amended auth test task.
  - `[low]` `[patch]` `/v1/health/ready` only asserted at schema level — amended readiness test task.
  - `[medium]` `[bad_spec]` `requeue_stale` has no caller — amended maintenance loop requirement.
  - `[high]` `[bad_spec]` (grouped: migration metadata) — same as migration finding above.
  - `[low]` `[patch]` (grouped: `include` ignored) — same as result-filter finding above.
  - `[medium]` `[patch]` (grouped: worker loop exits on claim error) — same as maintenance loop resilience finding above.

### 2026-11-01 — Review pass 2
- verdicts: 35 findings — high 2, medium 18, low 14, false 1, maybe-false 0
- findings:
  - `[low]` `[defer]` carried — README quick-start omits worker/maintenance — same location and claim as pass 1; the operational path remains a documentation follow-up.
  - `[medium]` `[patch]` `scripts/create_client.py` stores `--webhook-url` without intake validation — now runs `validate_webhook_url` and exits with the same remediation.
  - `[high]` `[patch]` `ensure_safe_target` lets `ValueError` escape for bad URLs/ports and the delivery loop spins — catches `ValueError`, validates the port at intake, and terminalises safely.
  - `[medium]` `[patch]` Transient DNS failure permanently kills a delivery — `EAI_AGAIN`/`EAI_FAIL`/empty resolution are retryable; only `EAI_NONAME` is terminal.
  - `[medium]` `[patch]` `probe_image` decodes every TIFF frame before the page cap — takes the remaining budget, reads `n_frames` first, caps `MAX_IMAGE_PIXELS`.
  - `[medium]` `[patch]` Multipart parsing runs synchronously on the event loop — parser feed moved to `asyncio.to_thread` with ordering preserved.
  - `[low]` `[patch]` Revoked-key branch unreachable; revoked keys get "not recognised" — auth now does a revoked-prefix lookup so the specific message is reachable, with a test.
  - `[medium]` `[patch]` Crash-requeue refunds attempts forever, so a crashing worker never reaches dead — added a bounded `requeues` counter; ceiling marks the task dead and terminalises file/submission.
  - `[low]` `[defer]` Committed `docs/openapi.yaml` still lists optional `job_url` while the 202 omits it — deliberate contract choice; docs reconciliation is a separate documentation pass.
  - `[false]` `[reject]` Webhook payload omits `decision` — the spec's boundary says payloads carry ids, counts and `result_url` only; the payload deliberately does not embed decision data.
  - `[low]` `[patch]` `include` enum invisible in generated OpenAPI — published as a pattern and asserted by the contract test.
  - `[low]` `[reject]` `include` values `checks`/`provenance`/`review` are no-ops and `review` always returns — documents are empty in this story and `docs/openapi.yaml` shows `review` in the default result; rejecting the values would break the documented enum, and returning the block is correct.
  - `[low]` `[patch]` Three tests assert weaker things than their names — renamed/tightened the `document_id`, DOCX-magic and document-count tests.
  - `[low]` `[defer]` Compose does not forward S3/pool settings — development compose intentionally uses filesystem storage; production config is env-driven outside compose.
  - `[low]` `[patch]` `.env` `TEST_DATABASE_URL` never reaches `os.getenv` — integration config now loads `.env`.
  - `[low]` `[defer]` carried — Dead surface (unused enums/helpers) ships — same claim as pass 1; cleanup belongs to the story that wires those states.
  - `[low]` `[patch]` Migration test does not compare models to migrated DDL — now runs `alembic check` inside the migration test.
  - `[medium]` `[patch]` `invalid_webhook_url` hardcodes HTTPS/2048 and the reason does not fit — remedy now derives from the configured limit and the specific rule.
  - `[low]` `[patch]` `build_result` discards `settings` and fabricates policy version 0 — unused parameter dropped; a missing policy raises `service_unavailable`.
  - `[low]` `[patch]` No coverage for scripts, S3 or storage guards — added script tests (bootstrap/create_client + printed key), duplicate-webhook assertion, and retained storage guard tests as follow-up scope.
  - `[medium]` `[patch]` Parser construction after `mkdtemp` leaks temp dir and returns 500 for a bad boundary — boundary validated 1–256 bytes and construction moved inside cleanup.
  - `[medium]` `[patch]` (grouped: image cap) — same as image-frame finding above.
  - `[medium]` `[patch]` (grouped: URL port) — same as `ensure_safe_target` finding above.
  - `[high]` `[patch]` (grouped: URL `ValueError`) — same as `ensure_safe_target` finding above.
  - `[low]` `[defer]` Readiness does not probe an S3 bucket or writable filesystem — an S3 liveness write is a later operational hardening item; filesystem readiness is checked.
  - `[medium]` `[patch]` Rollback after resurrect deletes a file's pre-existing storage key — resurrected files no longer add their prior key to the compensating-delete list.
  - `[medium]` `[patch]` (grouped: webhook remedy text) — same as `invalid_webhook_url` finding above.
  - `[medium]` `[patch]` Webhook 4xx/5xx dead-letter transitions untested — added 404 death at `min(3, max_attempts)` and 5xx death at the configured maximum.
  - `[medium]` `[patch]` Dead `finalize_submission` untested — added the case, asserting submission/job failed, polled reason and `result.failed` row.
  - `[medium]` `[patch]` Bootstrap script untested — added `test_scripts.py` running bootstrap against the migrated test DB, then readiness and a 202 submission.
  - `[medium]` `[patch]` `create_client` key never verified against auth — new test parses the printed key and submits with it.
  - `[medium]` `[patch]` PDF page-cap "stop before text extraction" unasserted — unit test asserts `page_cap_breached`, empty pages and the real count.
  - `[medium]` `[patch]` Constraint-race branches never executed — deterministic insert-race test forces the `IntegrityError` branch and asserts one 202 body, no 500, no duplicates.
  - `[low]` `[defer]` Docker image build/`boto3` install not part of pytest — the verification run builds both images and checks the extra; CI wiring is outside this story.
  - `[medium]` `[patch]` (grouped: webhook remedy) — same as `invalid_webhook_url` finding above.

### 2026-11-01 — Review pass
- verdicts: 70 findings — high 0, medium 20, low 47, false 3, maybe-false 0
- findings:
  - `[low]` `[defer]` (carried) README never starts a consumer — same location and claim as passes 1–2; the worker/maintenance commands remain documented only in `Dockerfile.worker` and `docker-compose.yml`.
  - `[low]` `[defer]` (carried) README omits the credential/scope/error contracts — the README documentation gap carries from pass 1; the unconditional `/docs` mount is FastAPI's default surface and exposes no tenant data.
  - `[low]` `[defer]` (carried) No CI or task runner — same claim as the pass-2 finding that CI wiring is outside this story; verification remains manual but documented.
  - `[low]` `[reject]` `mypy` is signed off for `domain` and `services` only — the fix is to edit this story's Verification section, and the typed core is exactly the surface the intent protects; `api`/`workers` are thin adapters.
  - `[low]` `[defer]` `.env.example` omits optional settings (`JOB_MAX_ATTEMPTS`, key-length bounds, `MAX_REQUEST_BYTES`) — an operator-editable documentation gap; every value has a validated default in `config.py` and no behaviour is wrong.
  - `[low]` `[defer]` AWS credentials in `.env` never reach boto3 — `Settings` does not export `env_file` values to `os.environ`, but production injects real environment variables, which boto3 reads; only a `.env`-file S3 development setup is affected.
  - `[low]` `[defer]` (carried) Compose cannot exercise S3 or the pool settings — same location and claim as the pass-2 deferral that production configuration is env-driven outside compose.
  - `[low]` `[patch]` `job_max_attempts` is declared but never read, while `tasks_repo` hard-codes `3` — `ingest` now passes `settings.job_max_attempts` into both task enqueues, so the configured limit is the one the task rows record.
  - `[low]` `[reject]` Webhook backoff schedule is a module tuple — the schedule is a delivery policy curve, not an intake limit; `WEBHOOK_MAX_ATTEMPTS` is configurable, and making the curve configurable adds tuning surface the intent does not ask for.
  - `[low]` `[reject]` (carried) `include` has three defaults and two no-ops — same location and claim as the pass-2 rejection: documents are empty in this story, the documented enum must keep working, and returning the block is correct.
  - `[low]` `[patch]` (carried) `document_id` filters an always-empty document list — same location and claim as the pass-1 `include`/`document_id` row; the filter is correct-shaped and becomes observable in story 2.
  - `[low]` `[reject]` `Cache-Control: no-store` is paired with `ETag`/304 — the story's own task list mandates `no-store` with `ETag` and `If-None-Match`, so the fix would edit this spec; a client may keep the validator in memory.
  - `[low]` `[defer]` Every `doc_url` points at `docs.trade-extract.example` — the error catalogue is a documentation deliverable with no host in this story; `docs_base_url` exists as the wiring point.
  - `[low]` `[defer]` OpenAPI `servers` are hard-coded rather than derived from `base_url` — cosmetic metadata on the published schema; no implemented route or decision depends on it.
  - `[low]` `[defer]` Generated OpenAPI does not declare the response headers the contract relies on — contract-documentation completeness; the headers are emitted and asserted at HTTP level.
  - `[low]` `[patch]` (carried) Contract test cannot see a documented-optional property the API omits — same location and claim as the pass-1 both-direction finding and the pass-2 `job_url` deferral.
  - `[low]` `[defer]` `Document` uses `extra="allow"`, so the contract test cannot see new response fields — forward-compatible response modelling; the committed schema and the route model are both asserted where it matters.
  - `[low]` `[reject]` Readiness discards its partial `checks` map — each failure path's message names the failing component (`database unavailable`, `object storage unavailable`, `no active policy`), so the operator is not left without evidence.
  - `[low]` `[patch]` The HTTP-exception mapper drops `exc.headers` — `_json` now accepts extra headers and the `StarletteHTTPException` handler forwards them, so a 405 keeps `Allow` and any `Retry-After` survives.
  - `[medium]` `[patch]` Webhook delivery recovery is unbounded and can wedge — grouped with the two edge-case members below: a failed decrypt escaped `deliver_once` and a reclaim refunded the attempt, so a rotated key or a crash loop retried forever with no dead-letter.
  - `[low]` `[defer]` 429 `Retry-After` ignored; 3xx burns the attempt budget; the 410 branch duplicates `_is_terminal_4xx` — the redirect-refusal is deliberate SSRF safety and the duplicated branch is harmless; honouring a target's `Retry-After` is later delivery hardening.
  - `[medium]` `[patch]` SSRF guard does not normalise IPv4-mapped IPv6 — `::ffff:127.0.0.1` reaches loopback while `is_loopback` is false on the IPv6 form; the guard now judges `ipv4_mapped` before its checks.
  - `[low]` `[reject]` Filenames reach logs and messages unsanitised — structured JSON logging escapes control characters, and the only echo is the caller's own filename back to the caller.
  - `[low]` `[reject]` Unknown multipart field names are silently ignored — the observed outcome is the documented `no_documents` refusal; naming the field would add a new intake branch for a caller typo.
  - `[low]` `[defer]` (carried) `domain/enums.py` is unused and duplicates DDL/model vocabularies — same claim as the pass-1/2 dead-surface deferral; cleanup belongs to the story that wires those states.
  - `[low]` `[defer]` (carried) Dead surface ships — same location and claim as the carried pass-2 dead-surface finding; the frontmatter ledger is orchestrator-owned and no existing entry was altered.
  - `[low]` `[reject]` `db/session.py` advertises a SQLite branch — never reached by the application or the tests; the branch is a configuration convenience, not a supported deployment.
  - `[low]` `[defer]` `alembic check` cannot compare CHECK constraints, partial indexes or triggers — a known Alembic limitation; the migration test asserts those objects directly on PostgreSQL.
  - `[low]` `[reject]` Frozen DDL permits task kinds and webhook events no code emits — the initial schema is deliberately forward-looking for story 2 (Design Notes), and the CHECK lists are the future contract.
  - `[low]` `[defer]` `create_client` grants unchecked scopes and can race its slug check — scope vocabulary is design intent pending the routes that need it; the slug race surfaces as a traceback rather than a clean message.
  - `[medium]` `[patch]` `bootstrap` crashes when a retired version 1 exists — grouped with the edge-case duplicate: it now takes `max(version) + 1`, so retiring the active policy produces v2 instead of a unique-violation failure that would fail the compose `migrate` service.
  - `[false]` `[reject]` Two fixtures encode wrong shapes — every caller of `seed_client` discards or ignores the literal `"policy"` third value, and `test_expired_key_is_401` really does assert 401 plus an `expired` message on `/v1/extract`; the readiness 200 is only a side-check.
  - `[low]` `[defer]` Integration fixtures run `downgrade base`/`TRUNCATE` against whatever `TEST_DATABASE_URL` names — a developer-safety guard on the test harness; the suite is documented as requiring a dedicated test database.
  - `[low]` `[patch]` `python-dotenv` is imported by `tests/integration/conftest.py` but declared only transitively — added to the `dev` extra so `uv sync --extra dev` installs it explicitly.
  - `[medium]` `[patch]` Branches added for correctness ship untested — the verified members (constraint-race retry, all-duplicates webhook) are covered by the new integration tests below; the remaining named branches (submission-level URL override, tenant isolation, `include=checks`, `payload_too_large` cleanup) are covered by the existing suites or are the same claims as the verification-gap rows.
  - `[medium]` `[patch]` `probe_image` only warns between the Pillow ceiling and twice it — grouped with the edge-case pixel finding: decoding is now refused above the 100-megapixel budget before `load()`, so a compressed 150-megapixel image cannot occupy gigabytes of texture store.
  - `[low]` `[defer]` `requeue_stale` lacks `SKIP LOCKED`, `active_count_for_job` counts in Python, and `last_used_at` writes on every request — concurrency and hot-row tuning with more than one maintenance replica; the documented deploy runs one.
  - `[low]` `[defer]` Compose API healthcheck polls liveness and worker/maintenance have none — development-compose operational polish; readiness is covered by its own route and test.
  - `[low]` `[reject]` The story's I/O matrix omits cases the code implements — the fix is to edit this spec, which is explicitly out of bounds for a review finding.
  - `[low]` `[reject]` The verification note conflates a unit test with an image check — again a spec-text correction; the image build was re-run as part of this pass and succeeded.
  - `[false]` `[reject]` All-new files losing the `ux_files_job_sha256` race leave the submission queued forever — unreachable: `_upsert_job` takes `SELECT … FOR UPDATE` on the job row via `jobs_repo.get_for_update`, so same-`job_ref` submissions serialise and no file can be inserted between `find_by_hashes` and `insert`; an attempted forced race deadlocks on that job lock, confirming it.
  - `[false]` `[reject]` Two concurrent resubmissions resurrect one failed file and strand the first — same refutation: the second submission blocks on the job row lock until the first commits and then reads the row as `received`, so it is counted as a duplicate.
  - `[medium]` `[patch]` `decrypt_webhook_secret` raises outside `deliver_once`'s handlers — a rotated or wrong `SECRET_KEY` left the row leased with no state change; the call is now wrapped and marks the delivery `dead` with the reason.
  - `[medium]` `[patch]` Reclaiming a stale delivery refunds its attempt forever — reclaim now spends one attempt and dead-letters at `WEBHOOK_MAX_ATTEMPTS`, which maintenance passes in; two integration tests cover the spend and the dead-letter.
  - `[medium]` `[patch]` `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` is accepted in production — settings validation now refuses it there, matching the existing production secret guards.
  - `[medium]` `[patch]` A frame between 100 and 200 megapixels is only warned about — `probe_image` now rejects any frame above the budget before decoding it.
  - `[medium]` `[patch]` `job_ref`/`Idempotency-Key` accept a trailing newline — `^…$` with `re.match` matches before a final newline; both validators now use `fullmatch`, and an HTTP test proves `job_ref="BL-1\n"` is a `400`.
  - `[medium]` `[patch]` A spooling `OSError`/`MemoryError` is reported as a caller-fault `400` — the spool now maps server-side storage failure to `service_unavailable`, so clients retry instead of abandoning a healthy file.
  - `[medium]` `[patch]` (grouped) `bootstrap` cannot recover from a retired version 1 — same fix as the blind-hunter row above; the new test retires v1 and asserts v2 becomes active.
  - `[medium]` `[patch]` (grouped) Validation-order bounds accept a trailing newline — same defect and fix as the edge-case row above.
  - `[medium]` `[patch]` The `(client, job_ref)` upsert race is never exercised — the new test forces a competing committed insert between lookup and insert, asserts the retry still returns `202` with one job and one submission. The duplicate-file half of the claim is refuted by the job lock.
  - `[medium]` `[patch]` The all-duplicates completion event is unverified — the new test submits with a webhook URL, resubmits identical bytes, and asserts exactly one pending `result.completed` outbox row.
  - `[medium]` `[patch]` Neither `run_forever` loop is executed by a test — new tests raise once and then `KeyboardInterrupt`, proving both loops survive a transient error and reach a second iteration.
  - `[medium]` `[patch]` A transport-level webhook failure is unverified — a new test drives a `ConnectError` through `run_webhook_once` and asserts `failed`, one attempt, a future `next_attempt_at`, and no `delivered_at`.
  - `[low]` `[patch]` The bootstrap already-active branch is asserted only by a substring — the branch is in fact executed (the fixture seeds an active policy), and the new test now asserts the exact `already exists` line, a version advance and the final `[(1, retired), (2, active)]` rows.
  - `[medium]` `[patch]` The catch-all 500 envelope handler is never exercised — a new integration test raises from the result repository and asserts the `internal_error` envelope, the echoed request id, and that the internal message never reaches the body.
  - `[low]` `[patch]` The client-level `webhook_enabled=False` opt-out is untested — `seed_client` accepts the flag and a new test proves a disabled client's stored URL receives nothing.
  - `[low]` `[defer]` Cross-rule precedence (which rule wins when one request breaks two) is untested — the intent fixes the order but not precedence within a streamed request; pinning it would enshrine an implementation choice rather than a requirement.
  - `[medium]` `[patch]` No test streams a group whose first file is valid and whose later file fails a post-spool rule — a new test submits one valid PDF and one over-page PDF under a lowered cap and asserts `page_limit_exceeded` with no job/submission/file/task row and no stored object.
  - `[low]` `[reject]` "No reader in the API package" could be read to forbid any parser — the intent's own multi-shipment rule requires the embedded text layer before storage, so a lazily imported PDF probe is the only reading that satisfies both; the Design Notes record the supersession and the OCR-free image is asserted.
  - `[low]` `[patch]` Temp-file cleanup on the success and ingest-exception paths is unasserted — a new test submits successfully and asserts the temp snapshot does not grow.
  - `[low]` `[patch]` Several request-path literals sit outside configuration — the wired member (`job_max_attempts`) is fixed; the remaining constants are internal safety bounds (field-memory cap, boundary length, pixel budget, lease) rather than caller-facing limits, and are recorded here as a bounded follow-up.
  - `[low]` `[patch]` `job_ref`/`Idempotency-Key` bounds are asserted only as unit calls — HTTP-level tests now cover the trailing newline and a 7-character key, both `400` with the offending field named.
  - `[low]` `[reject]` The per-document key is asserted as arithmetic, not behaviour — the Never list forbids creating document rows in this story; the schema constraint is asserted by a direct insert, which is the strongest available surface.
  - `[medium]` `[patch]` (grouped) The durable worker/maintenance loops are never run — same claim and fix as the verification-gap loop-resilience row.
  - `[low]` `[patch]` (carried) The committed contract is only checked in one direction where it matters — same location and claim as the carried contract-test rows.
  - `[low]` `[reject]` The matrix is asserted only when PostgreSQL is up — integration tests are marked and documented, and this verification run executed all 151 tests against PostgreSQL 16.
  - `[low]` `[patch]` (carried) `include`/`document_id` behaviour is unobservable — same location and claim as the carried `document_id` row.
  - `[low]` `[reject]` The schema is broader than story 1 — deliberate forward DDL recorded in Design Notes; the application creates no document/segment rows.
  - `[low]` `[reject]` The diff adds behaviour the intent does not name (`client_inactive`, `endpoint_not_found`, `result.failed`, SSRF blocking, resurrection, `min(3, max_attempts)`) — each is the direct consequence of an intent rule (one envelope, failure terminalisation, webhook safety) and is covered by its own test.

## Design Notes

- **Webhook delivery is in scope** because the story's own contract and CAP-1 say the result is resolvable by webhook; the blueprint sequences it as hardening, but a story-1 result with no event would not meet the stated intent. Delivery here is the minimal correct outbox + signed POST + retry schedule; review-console replay and delivery history are later.
- **Envelope-only completion**: no document rows exist until story 2 creates segments. A completed result at this stage has an empty `documents` array; the CAP-1 per-document payload criterion is met once segmentation lands. Stub completion exists so the async lifecycle, webhook and idempotency can be proven now.
- **Intake must inspect content** to enforce the multi-shipment rule before storage. `pypdfium2` (base dependency, per `stack.md`) is imported lazily inside `services/page_model.probe_container()`; this supersedes the blanket "API never imports pypdfium2" sentence in `docs/01-architecture.md` while preserving its real constraint — the API image contains no PaddleOCR or OpenCV.
- **B/L evidence rule (review pass 1)**: a candidate is the **single token immediately following** a `B/L`, `BILL OF LADING`, `OCEAN BILL OF LADING` or `WAYBILL` label, optionally separated by `NO`, `NUMBER`, `#` or `:`. Scanning stops at the first whitespace, slash or comma after that token. Normalise case and punctuation; require at least one letter and at least three digits. Dates (`2026-10-31`), seals, invoice numbers and container numbers elsewhere on the line are **not** evidence. Two distinct candidates still reject; zero or one proceeds. A master-and-house B/L can false-reject; that remains the accepted consequence of A-08 until A-06 is resolved.
- **Page caps are checked from the container's page count before text extraction** (review pass 1): a 501-page file or a group crossing 2,000 pages must be rejected without extracting text from every page. The probe accepts the caps and stops at the first breach; this is the "costs nothing extra" property the earlier note claimed.
- **Image containers are validated, not assumed** (review pass 1): magic bytes select the type, but JPEG/PNG/WebP/TIFF are decoded with Pillow. A truncated or undecodable image is `malformed_document` before storage; a multi-page TIFF contributes its real frame count to the page caps.
- **Failure is a terminal lifecycle state** (review pass 1): when a task exhausts its retries and goes `dead`, its file and submission become `failed`, the job reaches `failed` when no submission is active, polling returns `status: failed` with a human-readable reason, and a `result.failed` webhook is enqueued when a URL is configured. Maintenance requeues `running` tasks whose lock is stale and reclaims webhook rows stuck in `delivering`; a crash-requeue does not consume the retry budget.
- **The acceptance body is frozen** (review pass 1): the `202` response always reports `status: queued`, including on replay after completion and for the all-duplicates case; polling is what reveals `completed`. The response omits `job_url` until a job-read route exists, so the API never advertises an endpoint it does not serve.
- **Whole-request accounting** (review pass 1): every byte of the multipart body — including ignored parts and field values — counts against `max_request_bytes` (20 × per-file cap plus multipart overhead), returning `413 payload_too_large`. A request cannot stream unbounded data through an unused part name.
- **Concurrency** (review pass 1): a lost `(client, job_ref)` insert race retries the upsert; a duplicate-file race is counted in `duplicate_documents`; only the submission idempotency-key constraint maps to `409 idempotency_key_reused`. No constraint failure becomes a bare 500.
- **Migration integrity** (review pass 1): `0001_initial` is a frozen, literal DDL script. Tests upgrade an empty database with Alembic and assert the Postgres-specific extension, triggers and exclusion constraint; `Base.metadata.create_all` is a test-fixture convenience only where it does not replace the migration check.
- **Result response scope**: `include` is validated against the documented enum (`documents`, `conflicts`, `checks`, `provenance`, `review`, `decision`); unknown values are `400 invalid_request`. `document_id` filters the submission's documents (empty in this story) and an unknown id returns an empty list rather than being silently discarded.
- **202 replay** is rebuilt from persisted fields rather than stored verbatim: `status` is always `queued` at acceptance, and every other body field derives from the submission, job and counts.
- **Counts**: `submitted_documents` is the number of `documents` parts in the call (duplicates included); `duplicate_documents` is the subset already present in the job and not reprocessed. When `job_ref` is absent, the per-document key still uses an empty job-reference segment; uniqueness is scoped by the newly created `job_id`, so two ungrouped submissions cannot collide.

## Verification

**Commands:**
- `uv sync --extra dev` — expected: resolves and installs.
- `uv run ruff check .` and `uv run ruff format --check .` — expected: clean.
- `uv run mypy src/trade_extract/domain src/trade_extract/services` — expected: no errors.
- `uv run pytest -q` — expected: unit + contract suites pass without external services.
- `docker compose up -d postgres && uv run pytest -q -m integration` — expected: integration suites, including migration, worker-failure, image and lifecycle suites, pass against PostgreSQL 16.
- `uv run pytest -q tests/integration/test_migrations.py` — expected: an empty database upgrades, gains the extension/triggers/exclusion constraint, and downgrades cleanly.
- `uv run alembic upgrade head` — expected: clean migration from an empty database.
- `docker build -f Dockerfile.api -t trade-extract-api .` — expected: build succeeds; a test asserts `trade_extract.api.app` imports no OCR/vision libraries.

**Manual checks (if no CLI):**
- `docker compose up` serves `/v1/health` and `/v1/health/ready`; a 21-file upload returns the documented `too_many_documents` envelope and `var/storage` stays empty.

## Auto Run Result

**Summary:** Follow-up review pass on the completed story. Four review layers ran over the full diff since `2ca59784`; triage verified every new claim against the code, carried the eleven findings that repeat a logged location and claim, and patched twelve real defects with new regression coverage. No intent gap and no bad-spec loopback were needed, so the implementation was not re-derived.

**Files changed:**
- `src/trade_extract/services/limits.py` — `job_ref`/`Idempotency-Key` now use `fullmatch`, so a trailing newline is refused; a spooling `OSError`/`MemoryError` is a `503`, not a caller-fault `400`.
- `src/trade_extract/services/page_model.py` — image frames above the 100-megapixel budget are rejected before Pillow decodes them.
- `src/trade_extract/services/webhooks.py` — a decrypt failure dead-letters the delivery instead of escaping; stale-delivery reclaim spends one attempt and dead-letters at the configured maximum; IPv4-mapped IPv6 targets are judged as IPv4.
- `src/trade_extract/config.py` — `WEBHOOK_ALLOW_PRIVATE_TARGETS` is refused in production.
- `src/trade_extract/api/errors.py` — the HTTP-exception mapper forwards `exc.headers` (405 keeps `Allow`).
- `src/trade_extract/services/ingest.py` — task enqueue/rearm take `settings.job_max_attempts` instead of the hard-coded `3`.
- `src/trade_extract/workers/maintenance.py` — passes the webhook attempt ceiling into stale-delivery reclaim.
- `scripts/bootstrap.py` — the next policy version derives from `max(version) + 1`, so a retired v1 no longer crashes the compose `migrate` service.
- `pyproject.toml`, `uv.lock` — `python-dotenv` declared in the `dev` extra.
- `tests/integration/conftest.py` — `seed_client` can seed a disabled webhook.
- `tests/integration/test_extract_api.py` — job-ref upsert race, all-duplicates completion event, HTTP bounds, late-group rejection, success-path temp cleanup, catch-all 500 envelope.
- `tests/integration/test_webhooks.py` — transport failure stays retryable; a disabled client URL is never used.
- `tests/integration/test_worker_failures.py` — worker and maintenance loops survive a transient error; both reclaim outcomes (attempt spent, dead-letter at max).
- `tests/integration/test_scripts.py` — bootstrap is idempotent and advances the version instead of colliding.

**Review findings breakdown:** 70 findings — 20 medium, 47 low, 3 false. 28 entries were patched (20 medium, 8 low); 4 further patch rows were carried from earlier passes and left as previously logged; 19 deferred (13 new ledger entries, 6 carried); 19 rejected on their refutation (including every finding whose fix would edit this spec). Three findings were refuted outright: the two "concurrent file race strands a submission" claims are unreachable because `_upsert_job` holds a `SELECT … FOR UPDATE` on the job row for the whole acceptance transaction — an attempt to force the race deadlocked on that lock — and the "fixtures encode wrong shapes" claim does not hold at the cited lines.

**Follow-up review recommendation:** `false`. No patched entry carried a `high` verdict, so the follow-up pass has converged; patch volume alone is never a reason to continue.

**Verification performed:**
- `uv sync --extra dev` — resolved and installed (lock updated).
- `uv run ruff check .` — all checks passed.
- `uv run ruff format --check .` — 88 files already formatted.
- `uv run mypy src/trade_extract/domain src/trade_extract/services` — no issues in 13 source files.
- `uv run pytest -q` — 151 passed against PostgreSQL 16 (137 before this pass, 14 added).
- `uv run pytest -q tests/integration/test_migrations.py` — passed.
- `docker build -f Dockerfile.api -t trade-extract-api:review .` — build succeeded after the lockfile change.

**Residual risks:**
- The duplicate-file constraint branch remains defensive-only: the job-row lock serialises same-`job_ref` submissions, so it cannot be exercised; if the locking strategy changes, that branch is the untested path.
- Two medium fixes are concurrency-sensitive and verified only by single-process tests: stale-delivery reclaim and the job-ref upsert retry.
- Deferred items are low severity and recorded in the frontmatter ledger; no existing entry was modified, re-opened or rewritten.


