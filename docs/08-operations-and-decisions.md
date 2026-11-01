# 08 — Operations, Decisions and Risks

## 1. Operating model

v1 is a small, single-region deployment:

- Managed PostgreSQL 16 with point-in-time recovery.
- Two stateless API replicas behind TLS termination.
- One or more worker replicas; OCR is CPU-bound and memory-hungry.
- One maintenance process (webhook, retention, exports) or a leader-elected set.
- S3-compatible object storage with versioning and a lifecycle rule for non-current versions.
- Secrets from a managed secret store, never from the repository.

The same images run in development and production. Development differs only in environment,
secrets, database size and storage backend.

## 2. Configuration

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `development`, `test`, `production`. |
| `DATABASE_URL` | local | `postgresql+psycopg://…`. |
| `SECRET_KEY` | none | ≥32 chars; session signing and envelope encryption. |
| `API_KEY_PEPPER` | none | ≥32 chars; API-key hashing. |
| `BOOTSTRAP_CLIENT_NAME` | dev | Created only when no client exists and environment is not production. |
| `BOOTSTRAP_API_KEY` | dev | Shown once; never used in production. |
| `REVIEWER_USERNAME` / `REVIEWER_PASSWORD` | dev | Bootstrap reviewer; forced password change on first production login. |
| `STORAGE_BACKEND` | `filesystem` | `filesystem` or `s3`. |
| `STORAGE_DIR` | `./var/storage` | Development only. |
| `S3_BUCKET` / `S3_ENDPOINT_URL` / `S3_REGION` | — | Production storage. |
| `PROVIDER` | `mock` | `mock` or `deepseek`. |
| `DEEPSEEK_API_KEY` | — | Required when provider is `deepseek`. |
| `DEEPSEEK_VISION_MODEL` | `deepseek-flash` | Only this model may read images. |
| `DEEPSEEK_TEXT_MODEL` | `deepseek-v4-pro` | Text-only reasoning. |
| `MAX_DOCUMENTS_PER_GROUP` | `20` | Do not raise without a v2 chunking story. |
| `MAX_FILE_BYTES` | `26214400` | 25 MiB. |
| `MAX_PAGES_PER_FILE` | `500` | |
| `MAX_PAGES_PER_SUBMISSION` | `2000` | |
| `DOCUMENT_RETENTION_HOURS` | `6` | Starts at terminal. |
| `RETENTION_SWEEP_SECONDS` | `300` | |
| `WORKER_POLL_SECONDS` | `2` | |
| `JOB_MAX_ATTEMPTS` | `3` | |
| `WEBHOOK_MAX_ATTEMPTS` | `8` | |
| `WEBHOOK_TIMEOUT_SECONDS` | `10` | |
| `CORS_ORIGINS` | empty | Empty disables CORS. |

Validation fails fast at startup for missing required production secrets, implausible limits, or
`PROVIDER=deepseek` without a key.

## 3. Deployment runbook

### First deploy

1. Provision PostgreSQL and object storage.
2. Store secrets in the secret manager.
3. Push the API and worker images to the registry.
4. Run the migration job: `alembic upgrade head`.
5. Start the API; confirm `/v1/health` and `/v1/health/ready`.
6. Start one worker; confirm it claims a synthetic task.
7. Create the first client and API key via the CLI; deliver the key out of band.
8. Create the first reviewer; force a password change.
9. Activate the baseline policy.
10. Run a smoke submission with a known invoice; confirm pass and provenance.

### Routine deploy

1. Build images tagged with the commit SHA.
2. Run migrations as a one-shot job; migrations are backward-compatible with the previous app
   version during the rollout.
3. Roll the API replicas one at a time; readiness gates traffic.
4. Roll the workers; each finishes its current task before exiting.
5. Watch error rate, queue age and provider errors for 15 minutes.
6. Rollback is the previous image tag; schema rollback is forward-fix only.

### Key rotation

- API keys: create a new key, deliver it, monitor usage, revoke the old key after the client
  confirms. The API accepts both during the overlap.
- Webhook secrets: set a new secret, keep the old one valid for a 24-hour grace window, then remove
  it. The review console shows the last successful delivery.
- `SECRET_KEY`: rotate with a re-encryption job for webhook secrets and a session epoch bump. Do not
  rotate by dropping the old key without re-encrypting.

## 4. Monitoring and alerts

| Alert | Condition | Action |
|---|---|---|
| API 5xx rate | > 1% for 5 min | Check DB, storage, deploy. |
| API p95 latency | > 1 s for 10 min | Check DB pool and slow queries. |
| Queue oldest age | > 10 min | Scale workers; check provider latency. |
| Dead tasks | Any increase | Inspect error code; replay or fix. |
| Provider error rate | > 5% for 10 min | Check status page; fail over to retry/backoff. |
| Provider cost | Daily spend > budget | Inspect token usage per document type. |
| Webhook backlog | > 100 pending or oldest > 30 min | Check client endpoints and SSRF guard. |
| Retention overdue | `oldest_overdue_seconds > 1800` | Run sweep manually; check storage permissions. |
| Retention failures | Any increase | Inspect object keys; fix and re-run. |
| DB connections | > 80% of max | Reduce pool size or add replicas. |
| DB bloat | Table/index bloat > 30% | Vacuum/partition; investigate. |
| Disk | < 20% free | Expand or purge old partitions. |
| Unreadable spike | > 10% of pages for 1 h | Check intake quality; notify client. |
| Review queue age | Oldest > SLA | Staffing; escalate. |

Every alert links to a runbook section. An alert without an action is noise.

## 5. Incident runbooks

### 5.1 Queue is stuck

1. Check `v_retention_due`? No — check `job_tasks` counts by status.
2. If `queued` grows: are workers alive? Check worker logs and DB connections.
3. If `running` grows: stale visibility timeout. Run the reaper; confirm `locked_at` ages.
4. If one task repeatedly fails: inspect `task_attempts.last_error_code`, not document content.
5. If provider is down: pause worker claims for `extract_segment`, keep preparation running, and
   alert clients via status page. Do not mark documents failed until retries are exhausted.

### 5.2 Provider outage or rate limit

- The adapter retries `429`/`5xx` with jitter. If the circuit opens, `extract_segment` tasks remain
  queued and the queue age alert fires.
- Do not switch models silently. A model change is a policy/version decision and must be recorded.
- If the outage exceeds the SLA, communicate a delay; results are not fabricated.
- Peak pricing is 04:00–07:00 and 09:00–13:00 EAT. Backfills run off-peak; realtime intake cannot
  avoid peak.

### 5.3 Retention purge failing

1. Check `retention_sweeps` for the failed run and the error.
2. Verify storage credentials and bucket policy; a denied `DeleteObject` is the usual cause.
3. Check S3 versioning: non-current versions must be deleted or expired, or the purge is a fiction.
4. Re-run the sweep. It is idempotent.
5. If objects remain past the grace window, open a privacy incident; the promise is the product.

### 5.4 Webhook backlog

1. Check the oldest pending delivery and the last status code.
2. A client `5xx` or timeout: retries continue; notify the client.
3. A `410`: mark dead; do not retry.
4. A private-address rejection: the URL is unsafe; contact the client.
5. Replay a dead delivery only after confirming the payload and URL.

### 5.5 Database growth

1. Identify the largest tables: `provider_calls`, `field_candidates`, `audit_events`, `job_tasks`.
2. Apply retention jobs; partition the largest tables if vacuum is the bottleneck.
3. Never delete `documents`, `field_values` or `review_resolutions` to save space; they are the
   product's legal record.
4. Add indexes only after `EXPLAIN (ANALYZE, BUFFERS)`; drop unused indexes.

### 5.6 Data subject or deletion request

1. Verify the requester's authority.
2. Create a deletion request; record the scope and legal hold.
3. Export the extraction record first if the client asks.
4. Delete extraction records and object prefixes; keep the audit event and a tombstone.
5. State the effective date including backups. A deletion that ignores backups is not a deletion.

## 6. Backup and restore

- Postgres: daily full backup plus point-in-time recovery; test a restore monthly.
- Object storage: versioning on; non-current versions expire on the same schedule as files.
- Backups are encrypted and access-controlled; restore access is audited.
- The effective deletion date includes backups. State it in the DPA, not just in the runbook.
- A restore drill must prove the API can serve a result and the retention sweeper can purge after
  restore without resurrecting deleted files.

## 7. Security and compliance

- Kenya's Data Protection Act 2019 applies to personal data in trade documents. Store the minimum,
  purge files aggressively, and keep extraction records under a documented lawful basis.
- Cross-border transfer: confirm the hosting region and provider data-processing terms. The
  provider adapter makes the inference stack swappable; the DPA must name the subprocessors.
- No document content in logs, traces, error reports or metrics. A base64 blob in a debug line is a
  breach of the retention promise.
- Review console behind SSO or an identity-aware proxy; no public exposure.
- Annual access review for API keys, reviewer accounts and storage credentials.
- Penetration test before the first production client: upload parsing, IDOR, SSRF, CSRF, webhook
  signature, rate limits.

## 8. Capacity and cost

Rough unit economics for one page:

| Cost | Driver |
|---|---|
| Vision tokens | One page image ≈ 1024 tokens; crops add more. |
| Text tokens | Schema + OCR text + repair attempts. |
| OCR CPU | PaddleOCR ~0.5–2 s/page on CPU, more for tables. |
| Storage | Source + quality-85 JPEG page image + thumbnail ≈ 2–6 MB/page. |
| Review | The dominant cost. A document that costs $0.03 in tokens and 4 minutes of a clerk is a $2 document. |

Capacity rules:

- A worker replica handles roughly 1–2 pages/minute for mixed scans on 2 CPU / 4 GB; measure, do
  not assume.
- Scale workers on queue age, not CPU alone.
- Keep prompts byte-stable per document type; cache hits are roughly one twentieth the input price.
- Backfill off-peak where possible; realtime intake cannot avoid peak.
- The review rate is the business model. Measure it from `client_usage_daily` and decisions.

## 9. Decision log

### ADR-001 — One Python codebase, OCR isolated in the worker

**Decision:** Python 3.12 for API and worker; PaddleOCR, OpenCV and pypdfium2 only in the worker
image.

**Why:** PaddleOCR is Python. A second language or an internal OCR RPC adds cost without removing
the dependency. Image separation gives the isolation benefit without a polyglot split.

**Consequence:** The API image is slim and deployable independently; the worker image is large and
pinned. A test asserts no OCR import in the API.

### ADR-002 — PostgreSQL is the queue

**Decision:** `job_tasks` with `FOR UPDATE SKIP LOCKED`, retries, visibility timeout and
dead-lettering.

**Why:** One datastore, one transaction boundary, durable jobs, no Redis. At v1 volume, Celery earns
nothing.

**Consequence:** Queue throughput is bounded by Postgres. Revisit when measured; partition or move
to a broker only then.

### ADR-003 — Sync SQLAlchemy and sync worker

**Decision:** SQLAlchemy 2.0 sync sessions with psycopg 3; sync FastAPI handlers where DB is touched;
sync worker.

**Why:** Obvious transaction boundaries, one dialect, no async upgrade in the most sensitive code.
The API is stateless and scales horizontally; the worker is CPU-bound.

**Consequence:** The API threadpool is a limit. More replicas are the first fix; an async rewrite is
a measured decision, not a default.

### ADR-004 — Object storage abstraction, no MinIO

**Decision:** `ObjectStorage` protocol with filesystem and S3 implementations. Production uses
Cloudflare R2 or AWS S3; development uses the filesystem.

**Why:** Files do not belong in Postgres. MinIO's repository was archived in April 2026, is
source-only and AGPLv3. Starting a new project on it is a bad bet.

**Consequence:** Swapping storage is configuration, not a code change. S3 versioning must be
lifecycle-managed or it defeats the purge.

### ADR-005 — Jinja2 + HTMX review console

**Decision:** Server-rendered fragments, no SPA, no build step.

**Why:** A handful of desktop reviewers; faster to build, fewer moving parts, one deployment.

**Consequence:** If a customer portal appears, it is a separate application. This decision does not
block it.

### ADR-006 — Normalized provenance, immutable extraction record

**Decision:** Provenance is a normalized bbox plus page index, page dimensions, reader and document
hash. The extraction snapshot is immutable; corrections are resolution records.

**Why:** A client can overlay provenance on its own copy after the platform purges the file. An
in-place edit would destroy the audit trail.

**Consequence:** Every field write carries provenance; a missing box fails validation.

### ADR-007 — Stated strings, internal normalized values

**Decision:** Public `value` is the exact stated string; internal `normalized_value` is used only by
checks and conflicts.

**Why:** "As stated" is the product promise. Numeric parsing is a validation oracle, not data.

**Consequence:** Clients parse with `value_type`; no computed duty, VAT or CIF is ever emitted.

### ADR-008 — Deterministic gate, confidence only orders

**Decision:** Only deterministic checks and falsifiable signals decide pass/review. Model confidence
may sort the queue.

**Why:** Confidence is uncalibrated and drifts with model and prompt changes.

**Consequence:** The invoice gate is explainable; a reviewer can see the failed check and both values.

### ADR-009 — Conflicts are never resolved by the platform

**Decision:** Report the canonical field and every disagreeing source; never pick a winner.

**Why:** The papers genuinely disagree; the business call belongs to the licensed agent.

**Consequence:** Review resolution records the client's decision but does not mutate stated values.

### ADR-010 — Files purge at six hours; extraction records outlive files

**Decision:** Terminal starts the six-hour clock. The extraction record is retained for the life of
the account plus ninety days, exportable and deletable on request.

**Why:** The record answers a dispute months later; the file is a privacy liability.

**Consequence:** A scheduled sweeper with metrics, alerts and an idempotent retry. S3 non-current
versions expire on the same schedule.

### ADR-011 — Provider adapter; DeepSeek split

**Decision:** Every model call goes through a provider adapter. Vision uses `deepseek-flash`; the
text-only `deepseek-v4-pro` is used only for reasoning over extracted text.

**Why:** `deepseek-v4-pro` cannot read images. The adapter keeps the inference stack swappable.

**Consequence:** No provider type crosses the boundary. Prompts are byte-stable per document type.

### ADR-012 — No automatic trust promotion in v1

**Decision:** Trust records are written per client/supplier/type/fingerprint; v1 always reviews at
the policy depth. A human correction demotes; nothing promotes automatically.

**Why:** There is no measured volume or accuracy baseline to justify promotion.

**Consequence:** The ramp is scaffolding, not a live optimisation. The policy dial exists and is
versioned.

### ADR-013: Packing list is a proposed eighth type

**Decision:** Treat `packing_list` as a first-class document type in the schema registry, pending
product sign-off.

**Why:** The SPEC names seven types, but CAP-2 requires a mixed PDF with a packing list to yield
three documents, and CAP-11 requires a 200-row packing list to be read completely. Neither is
implementable without a packing-list schema.

**Consequence:** If the product must hold to seven, CAP-2 and CAP-11 must be amended and the packing
list content folded into an invoice extension. That is a worse contract and should be an explicit
product decision, not a silent omission.

### ADR-014 — Terminal means completed or failed with no open review

**Decision:** `terminal_at` is set when the job is completed or failed and has zero open review items
and zero open conflicts. The six-hour file clock starts there.

**Why:** The SPEC says terminal means posted or review-resolved. v1 does not post to an ERP, so
completion is the delivery event. A job waiting on review must not lose its files before a reviewer
opens it.

**Consequence:** A completed result with no review starts the clock immediately; a review-resolved
job starts it at resolution. A failed job is terminal after retries are exhausted.

## 10. Spec ambiguities and open decisions

| ID | Issue | Current resolution | Owner |
|---|---|---|---|
| A-01 | Packing list is required by CAP-2/CAP-11 but absent from the seven types. | Proposed eighth type, ADR-013. | Product |
| A-02 | “v1 runs entirely at full review” vs the invoice pass gate. | Trust ramp is off; invoice may still pass on checks. The statement is read as “no trust-based reduction”, not “no deterministic pass”. | Product |
| A-03 | “Terminal means posted or review-resolved”, but v1 has no ERP posting. | Completion is delivery; ADR-014. | Product / engineering |
| A-04 | Email may alert, but no email provider is in the stack. | v1 has no email; the review console is the alert surface. Add an email adapter later without changing resolution. | Product |
| A-05 | Custom-entry field set is unverified. | Implemented as data with an explicit provisional note; confirm against iCMS before contractual use. | Product / clearing agent |
| A-06 | Master vs house B/L is unresolved. | `bl.form` carries the distinction; v1 does not infer it. | Product |
| A-07 | Supplier identity for trust records may be absent. | `supplier_key` falls back to a normalized seller name or `unknown`; a correction on `unknown` demotes only that bucket. | Engineering |
| A-08 | Multi-shipment detection has no specified algorithm. | Positive-evidence guard: two distinct normalized B/L numbers rejects; ambiguity proceeds to review. | Engineering |
| A-09 | “Four processes” in the stack vs the service list. | Three runtime processes (api, worker, maintenance) plus Postgres; `web` is optional and unused. | Engineering |
| A-10 | Review-rate target is unset. | The policy dial exists; the number is set from the eval corpus, not in code. | Product |
| A-11 | Data residency and cross-border transfer are unspecified. | Confirm hosting region and provider terms before the first production client. | Legal / product |
| A-12 | Provider pricing and model capability move. | Re-verify `provider-notes.md` before any contract or costing decision; the adapter makes a swap a config change. | Engineering |

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Accuracy on stamps, handwriting, rotated scans and merged cells is unmeasured. | High | High | Build the labelled corpus first; report per-field accuracy; refuse unreadable. |
| Review rate makes the unit economics unworkable. | Medium | High | Measure on the corpus; policy dial; invoice gate only where deterministic. |
| Silent table truncation looks like success. | Medium | High | Table completeness metadata; unresolved rows; tests with a 200-row packing list. |
| S3 versioning defeats the purge. | Medium | High | Lifecycle rule for non-current versions; retention alert. |
| Webhook payload leaks document content to a relay. | Low | High | Payload contains IDs and counts only; result fetched with the client key. |
| PII in logs or error reports. | Medium | High | Structured logging contract; scrub tests; no content in provider ledger. |
| Provider outage or rate limit. | Medium | Medium | Retries, queue backpressure, status communication; no fabricated output. |
| Schema drift between prompt, validator and published contract. | Medium | Medium | One Pydantic model generates schema and validates; contract tests; version bump on meaning change. |
| Queue starvation or stale tasks. | Medium | Medium | Partial index, visibility timeout, reaper, queue-age alert. |
| Postgres becomes the bottleneck. | Low at v1 | Medium | Short transactions, partition large tables, scale vertically, revisit broker only when measured. |
| File-parser vulnerability. | Low | High | Magic-byte checks, parser limits, non-root worker, no shell, egress restriction, pen test. |
| Reviewer error becomes labelled truth. | Medium | Medium | Reason required; correction history; audit; eval compares against source, not reviewer alone. |
| Deletion request ignores backups. | Medium | High | State effective date including backups; restore drill; legal review. |

## 12. Assumptions and non-goals

Assumptions carried from the SPEC:

- Every document in a group relates to one bill of lading, asserted by the caller.
- The field schema is invariant across layouts; only the mapping varies.
- Real Kenyan trade documents are obtainable from a working clearing agent.
- A large fraction of intake is digital PDFs with a text layer.
- v1 buyers are technical and can call an API.
- DeepSeek is the first provider; `deepseek-v4-pro` is text-only.
- Model choice is provisional until the eval harness measures accuracy.

Non-goals:

- No email or mailbox ingestion, no grouper, no fuzzy job assembly.
- No ERP or customs posting in v1.
- No on-premise or local inference stack.
- No duty, levy, VAT or CIF computation.
- No batch or multi-shipment intake.
- No automated trust promotion.
- No general-purpose document AI beyond the defined types.

## 13. Release checklist

- [ ] `docs/openapi.yaml` validates and matches the FastAPI schema.
- [ ] Migrations run from empty and from the previous release.
- [ ] API image has no OCR imports; worker image has all OCR extras pinned.
- [ ] Smoke test: submit an invoice, poll a result, verify a provenance box.
- [ ] Conflict test: invoice 18,450 vs packing list 18,540 → one conflict, both sources.
- [ ] Unreadable test: a blurred page returns a reason, not fields.
- [ ] Table test: 200 rows all present or explicit unresolved rows.
- [ ] Retention test: terminal + 6 h purges objects; extraction record remains.
- [ ] Webhook test: signature verifies; a failed delivery retries; dead delivery is visible.
- [ ] Review test: two queues, separate counts, resolution records.
- [ ] Security test: IDOR, scope, CSRF, SSRF, magic bytes, rate limit.
- [ ] Eval report attached with sample sizes and real/synthetic separation.
- [ ] Rollback plan and on-call contact documented.
