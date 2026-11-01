### DW-1: `.env.example` omits several optional settings that operators can tune.
origin: spec-deferred d2ac8d6c53f6
location: .env.example
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `JOB_MAX_ATTEMPTS`, `MAX_JOB_REF_LENGTH`, `MIN_IDEMPOTENCY_KEY_LENGTH`, `MAX_IDEMPOTENCY_KEY_LENGTH`, `MAX_WEBHOOK_URL_LENGTH` and `MIGRATION_TEST_DATABASE_URL` are read but not documented; every one has a validated default, so no behaviour is wrong, but an operator cannot discover them.
status: open

### DW-2: AWS credentials supplied through a `.env` file never reach the boto3 client.
origin: spec-deferred 5c44189b5ee7
location: src/trade_extract/services/storage.py:115
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `Settings` uses `extra="ignore"` and does not export `env_file` values into `os.environ`, while `S3Storage` builds a plain `boto3.client("s3")`. Production injects real environment variables, which boto3 reads, so only a `.env`-file S3 development setup is affected.
status: open

### DW-3: Every error `doc_url` points at an example host that serves no catalogue.
origin: spec-deferred 540624e794fd
location: src/trade_extract/config.py:112
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `domain/errors.py` and `config.docs_base_url` both hard-code `https://docs.trade-extract.example/errors`; the error catalogue is a documentation deliverable with no host in this story.
status: open

### DW-4: Published OpenAPI `servers` are hard-coded instead of deriving from `base_url`.
origin: spec-deferred a2f3bebdf741
location: src/trade_extract/api/openapi.py:30
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `api/openapi.py` lists `https://api.trade-extract.example` and `http://localhost:8000` although `settings.base_url` is what produces `status_url` and `result_url`; cosmetic metadata on the published schema.
status: open

### DW-5: Generated OpenAPI does not declare the response headers the polling contract uses.
origin: spec-deferred d12c710e1c0a
location: src/trade_extract/api/openapi.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `Location`/`Retry-After` on the 202 and `Retry-After`/`ETag`/`Cache-Control` and `X-Request-Id` on results are emitted and asserted at HTTP level but absent from the generated schema, so a generated client cannot discover them.
status: open

### DW-6: The `Document` response model allows extra fields, so the contract test cannot see new response members.
origin: spec-deferred b6ed6906a18e
location: src/trade_extract/api/schemas/results.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `ConfigDict(extra="allow")` publishes `additionalProperties: true`; a future field could appear in responses without failing the two-direction contract check.
status: open

### DW-7: Webhook delivery handling ignores some transient signals and a redirect status.
origin: spec-deferred aa95f977788e
location: src/trade_extract/services/webhooks.py:377
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: A 429 is retried on the fixed schedule without honouring its `Retry-After`, and a 3xx is neither success nor terminal so a permanently redirecting endpoint spends the whole attempt budget; redirect refusal itself is deliberate SSRF safety.
status: open

### DW-8: The migration drift guard cannot compare the Postgres-specific objects this schema depends on.
origin: spec-deferred d27ccf8fc837
location: tests/integration/test_migrations.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `alembic check` does not compare CHECK constraints, partial or expression indexes, the `segments` exclusion constraint or triggers, so models and the frozen DDL could diverge on exactly those constructs. The migration test asserts them directly against PostgreSQL, which mitigates but does not close the gap.
status: open

### DW-9: `create_client` grants scopes no route checks and can race its slug uniqueness.
origin: spec-deferred 8cf532e6e58a
location: scripts/create_client.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `DEFAULT_SCOPES` includes `account:read`/`account:write` that nothing requires, and the slug check is read-then-write, so a concurrent create surfaces as an `IntegrityError` traceback rather than the script's usual remediation message.
status: open

### DW-10: Integration fixtures are destructive with no test-database guard.
origin: spec-deferred bbc09a0944ef
location: tests/integration/conftest.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `db_engine` runs `alembic downgrade base` and `clean_database` truncates every table against whatever `TEST_DATABASE_URL` resolves to; a mis-set variable would drop a real schema. The suite is documented as requiring a dedicated database.
status: open

### DW-11: Queue and request-path concurrency tuning is unaddressed.
origin: spec-deferred c4b510347ecd
location: src/trade_extract/services/queue.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: `requeue_stale` uses a legacy query without `FOR UPDATE SKIP LOCKED`, so two maintenance replicas can both refund attempts; `active_count_for_job` materialises rows in Python; `get_api_key` writes `last_used_at` on every request. The documented deploy runs a single maintenance process.
status: open

### DW-12: Compose health and restart policy are uneven.
origin: spec-deferred cdfeb46018cb
location: docker-compose.yml:67
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: The API healthcheck polls liveness rather than readiness, so a container with no database or policy reports healthy; worker and maintenance have no healthcheck and no restart policy. This is development-compose polish.
status: open

### DW-13: Cross-rule precedence is not pinned when one streamed request breaks two rules.
origin: spec-deferred 7c9660213c8f
location: tests/integration/test_extract_api.py
source_spec: `1-api-contract-async-lifecycle-idempotency-and-refusal-guardrails.md`
severity: low
reason: The intent fixes the validation order but not which of two simultaneously broken rules wins inside one request; a test would enshrine an implementation choice rather than a documented requirement.
status: open

### DW-14: The file row lock and its database transaction are held across the whole preparation pipeline, including source download, full rasterisation, two storage uploads per page and segmentation.
origin: spec-deferred 34f3bfa37c4c
location: src/trade_extract/workers/handlers/prepare_file.py:136
source_spec: `2-page-model-and-always-on-segmentation.md`
severity: low
reason: `prepare_file.handle` calls `files_repo.get_for_update` before `_download_source` and `_render_and_store`, and the runner commits only after the handler returns. A large PDF at 300 DPI can therefore hold the lock and a pooled connection for minutes. Correctness is preserved (two deliveries cannot write two page sets) and the documented v1 deploy runs one worker, but throughput and connection-pool pressure need a later redesign — render outside the lock, then lock and re-check for an existing page set before writing.
status: open
