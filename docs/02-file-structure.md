# 02 — File Structure

## 1. Repository tree

```text
documentation-agent-api/
├── .env.example
├── .dockerignore
├── .gitignore
├── README.md
├── pyproject.toml
├── uv.lock
├── alembic.ini
├── docker-compose.yml
├── Dockerfile.api
├── Dockerfile.worker
├── docs/
│   ├── README.md
│   ├── 01-architecture.md
│   ├── 02-file-structure.md
│   ├── 03-database-schema.md
│   ├── 04-api.md
│   ├── 05-ui-architecture.md
│   ├── 06-document-schemas.md
│   ├── 07-implementation-blueprint.md
│   ├── 08-operations-and-decisions.md
│   └── openapi.yaml
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py
├── src/
│   └── trade_extract/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── logging.py
│       ├── cli.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   ├── deps.py
│       │   ├── errors.py
│       │   ├── middleware.py
│       │   ├── openapi.py
│       │   ├── routes/
│       │   │   ├── __init__.py
│       │   │   ├── health.py
│       │   │   ├── extract.py
│       │   │   ├── results.py
│       │   │   ├── jobs.py
│       │   │   ├── documents.py
│       │   │   ├── policy.py
│       │   │   ├── usage.py
│       │   │   ├── account.py
│       │   │   └── admin.py
│       │   └── schemas/
│       │       ├── __init__.py
│       │       ├── common.py
│       │       ├── extract.py
│       │       ├── results.py
│       │       ├── jobs.py
│       │       ├── documents.py
│       │       ├── errors.py
│       │       └── account.py
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── enums.py
│       │   ├── ids.py
│       │   ├── hashing.py
│       │   ├── clock.py
│       │   ├── provenance.py
│       │   ├── fields.py
│       │   ├── document.py
│       │   ├── checks.py
│       │   ├── conflicts.py
│       │   ├── decisions.py
│       │   ├── policies.py
│       │   ├── trust.py
│       │   ├── errors.py
│       │   └── canonical.py
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── registry.py
│       │   ├── common.py
│       │   ├── invoice.py
│       │   ├── proforma.py
│       │   ├── bill_of_lading.py
│       │   ├── delivery_order.py
│       │   ├── port_pass.py
│       │   ├── custom_entry.py
│       │   ├── verification_memo.py
│       │   └── packing_list.py
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── session.py
│       │   ├── models/
│       │   │   ├── __init__.py
│       │   │   ├── client.py
│       │   │   ├── api_key.py
│       │   │   ├── reviewer.py
│       │   │   ├── policy.py
│       │   │   ├── job.py
│       │   │   ├── submission.py
│       │   │   ├── file.py
│       │   │   ├── page.py
│       │   │   ├── segment.py
│       │   │   ├── document.py
│       │   │   ├── field.py
│       │   │   ├── check.py
│       │   │   ├── conflict.py
│       │   │   ├── decision.py
│       │   │   ├── review.py
│       │   │   ├── trust.py
│       │   │   ├── task.py
│       │   │   ├── webhook.py
│       │   │   ├── provider_call.py
│       │   │   ├── audit.py
│       │   │   └── retention.py
│       │   └── repositories/
│       │       ├── __init__.py
│       │       ├── clients.py
│       │       ├── jobs.py
│       │       ├── submissions.py
│       │       ├── documents.py
│       │       ├── fields.py
│       │       ├── conflicts.py
│       │       ├── reviews.py
│       │       ├── tasks.py
│       │       └── webhooks.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── ingest.py
│       │   ├── limits.py
│       │   ├── storage.py
│       │   ├── queue.py
│       │   ├── pipeline.py
│       │   ├── page_model.py
│       │   ├── conditioning.py
│       │   ├── segmentation.py
│       │   ├── readers/
│       │   │   ├── __init__.py
│       │   │   ├── base.py
│       │   │   ├── text_layer.py
│       │   │   ├── paddle.py
│       │   │   ├── vision.py
│       │   │   └── routing.py
│       │   ├── extraction.py
│       │   ├── field_merge.py
│       │   ├── validation/
│       │   │   ├── __init__.py
│       │   │   ├── engine.py
│       │   │   ├── normalize.py
│       │   │   ├── common.py
│       │   │   ├── invoice.py
│       │   │   └── custom_entry.py
│       │   ├── conflicts.py
│       │   ├── decisions.py
│       │   ├── review.py
│       │   ├── trust.py
│       │   ├── webhooks.py
│       │   ├── retention.py
│       │   ├── exports.py
│       │   └── audit.py
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── deepseek.py
│       │   ├── mock.py
│       │   ├── factory.py
│       │   └── prompts/
│       │       ├── segmentation.md
│       │       ├── invoice.md
│       │       ├── proforma.md
│       │       ├── bill_of_lading.md
│       │       ├── delivery_order.md
│       │       ├── port_pass.md
│       │       ├── custom_entry.md
│       │       ├── verification_memo.md
│       │       └── packing_list.md
│       ├── review/
│       │   ├── __init__.py
│       │   ├── routes.py
│       │   ├── auth.py
│       │   ├── csrf.py
│       │   ├── forms.py
│       │   ├── filters.py
│       │   ├── templates/
│       │   │   ├── base.html
│       │   │   ├── login.html
│       │   │   ├── dashboard.html
│       │   │   ├── queues/
│       │   │   │   ├── extraction.html
│       │   │   │   ├── conflicts.html
│       │   │   │   └── _rows.html
│       │   │   ├── documents/
│       │   │   │   ├── workspace.html
│       │   │   │   ├── _fields.html
│       │   │   │   ├── _checks.html
│       │   │   │   ├── _candidates.html
│       │   │   │   └── _viewer.html
│       │   │   ├── conflicts/
│       │   │   │   ├── workspace.html
│       │   │   │   └── _sources.html
│       │   │   ├── jobs/
│       │   │   │   ├── detail.html
│       │   │   │   └── _timeline.html
│       │   │   ├── admin/
│       │   │   │   ├── policies.html
│       │   │   │   ├── clients.html
│       │   │   │   ├── reviewers.html
│       │   │   │   └── retention.html
│       │   │   └── components/
│       │   │       ├── badge.html
│       │   │       ├── pagination.html
│       │   │       ├── toast.html
│       │   │       └── confirm.html
│       │   └── static/
│       │       ├── review.css
│       │       └── review.js
│       └── workers/
│           ├── __init__.py
│           ├── __main__.py
│           ├── runner.py
│           ├── heartbeat.py
│           ├── maintenance.py
│           └── handlers/
│               ├── __init__.py
│               ├── prepare_file.py
│               ├── segment_file.py
│               ├── extract_segment.py
│               ├── finalize_submission.py
│               ├── deliver_webhook.py
│               └── purge_documents.py
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_ids.py
│   │   ├── test_provenance.py
│   │   ├── test_field_merge.py
│   │   ├── test_invoice_checks.py
│   │   ├── test_conflicts.py
│   │   ├── test_decisions.py
│   │   └── test_canonical.py
│   ├── integration/
│   │   ├── test_extract_api.py
│   │   ├── test_pipeline.py
│   │   ├── test_queue.py
│   │   ├── test_retention.py
│   │   ├── test_webhooks.py
│   │   └── test_review_ui.py
│   ├── contract/
│   │   ├── test_openapi.py
│   │   ├── test_error_envelope.py
│   │   └── test_document_schemas.py
│   ├── eval/
│   │   ├── run_eval.py
│   │   ├── metrics.py
│   │   └── corpus/
│   │       └── README.md
│   └── fixtures/
│       ├── pdfs/
│       ├── images/
│       ├── provider/
│       └── golden/
├── scripts/
│   ├── bootstrap.py
│   ├── seed_dev.py
│   ├── eval.py
│   ├── create_client.py
│   └── purge_check.py
└── var/
    └── storage/            # development filesystem storage, git-ignored
```

## 2. Module responsibilities

### `src/trade_extract/api/`

The HTTP edge. It parses requests, authenticates, validates limits, shapes responses and emits
OpenAPI. It may read a stream to validate a file, but it does not parse pages, run OCR, build
prompts, or evaluate checks. Every route is a thin adapter over a service.

| File | Responsibility |
|---|---|
| `app.py` | FastAPI factory, lifespan, middleware, router registration, exception handlers. |
| `deps.py` | Request-scoped DB session, current client, current reviewer, pagination. |
| `errors.py` | Error envelope and exception-to-response mapping. |
| `middleware.py` | Request ID, access log, rate limit, body-size guard, security headers. |
| `openapi.py` | Stable OpenAPI tags, examples and schema customisation. |
| `routes/extract.py` | `POST /v1/extract`. |
| `routes/results.py` | Result polling and result documents. |
| `routes/jobs.py` | Job detail, job documents, job conflicts. |
| `routes/documents.py` | Single document retrieval. |
| `routes/policy.py` | Active policy summary. |
| `routes/usage.py` | Quota and usage. |
| `routes/account.py` | Export and deletion requests. |
| `routes/admin.py` | Internal admin JSON, protected separately from public keys. |

### `src/trade_extract/domain/`

Pure business vocabulary. No SQLAlchemy, no FastAPI, no httpx, no boto3. It defines IDs, enums,
provenance, field values, canonical keys, check results, conflict rules, decision rules, policy
objects and domain errors. It is the only package with a strict type-checking gate.

### `src/trade_extract/schemas/`

Pydantic v2 models for the seven document types plus the proposed packing list. Each model:

- Derives from a shared `DocumentEnvelope` and `Field` base.
- Declares required and optional fields with canonical field paths.
- Generates the JSON Schema used in the provider prompt **and** validates the provider response.
- Contains no I/O and no provider-specific types.

`registry.py` maps `document_type` → model, schema version, prompt file and check set. Adding a
type is a registry entry plus a model and a prompt, not a branch in the pipeline.

### `src/trade_extract/db/`

SQLAlchemy 2.0 models and repositories. Models mirror the DDL in
[`03-database-schema.md`](03-database-schema.md). Repositories expose intention-revealing methods
(`claim_task`, `upsert_job`, `list_open_conflicts`) and never return ORM objects across a
transaction boundary where a DTO is safer.

### `src/trade_extract/services/`

The application layer. It owns transactions, orchestration and the rules that are not pure domain
rules. Services accept a `Session`, a clock and adapters; they do not construct global clients.

| Service | Responsibility |
|---|---|
| `ingest.py` | Validate a submission, store files, create job/submission/file rows, enqueue tasks. |
| `limits.py` | Group, file, page and type limits, magic-byte sniffing, multi-shipment guard. |
| `storage.py` | `ObjectStorage` protocol plus filesystem and S3 implementations. |
| `queue.py` | Claim/heartbeat/retry/dead-letter; stale-task recovery. |
| `pipeline.py` | Stage orchestration and task fan-out/fan-in. |
| `page_model.py` | File → page rows, text layer capture, page metadata. |
| `conditioning.py` | Image correction and legibility scoring. |
| `segmentation.py` | Always-on page classification and segment grouping. |
| `readers/` | Reader interface, text-layer, PaddleOCR, vision and routing policy. |
| `extraction.py` | Reader invocation, schema validation, repair, field persistence. |
| `field_merge.py` | Candidate grouping, agreement, selection and provenance. |
| `validation/` | Deterministic checks and normalisation. |
| `conflicts.py` | Canonical-key comparison and conflict upsert. |
| `decisions.py` | Policy application and decision records. |
| `review.py` | Review item creation, assignment, resolution records. |
| `trust.py` | Trust record resolution and demotion. |
| `webhooks.py` | Signing, outbox, delivery and retry. |
| `retention.py` | Terminal-clock sweeper and object purge. |
| `exports.py` | Account export and deletion-request workflows. |
| `audit.py` | Append-only audit events. |

### `src/trade_extract/providers/`

One adapter protocol, two implementations: `deepseek.py` and `mock.py`. Prompts live in
`providers/prompts/` as byte-stable templates. The adapter translates domain requests to the
provider wire format and back; no provider type crosses the boundary.

### `src/trade_extract/review/`

Jinja2 templates, HTMX routes, session auth, CSRF, filters and a single vanilla JS file. The review
UI reads the same services as the JSON API and never talks to the database directly.

### `src/trade_extract/workers/`

The runner claims tasks, dispatches to a handler and records the outcome. Handlers are idempotent.
`maintenance.py` runs the webhook, retention and stale-task loops. The worker is the only image
that imports `paddleocr`, `cv2`, `numpy` or `pypdfium2`.

## 3. Import boundaries

These rules are enforced in CI with a small import-linter check (or a grep-based test if the
dependency is not worth adding):

1. `domain` imports only the standard library and Pydantic. No `db`, `services`, `providers`, `api`.
2. `schemas` imports `domain` and Pydantic. No `db`, `services`, `providers`, `api`.
3. `providers` imports `domain` and `schemas`. Never `db`, `services`, `api`.
4. `services` imports `domain`, `schemas`, `db`, `providers`. Never `api`, `review`, `workers`.
5. `api` and `review` import `services`, `schemas`, `db`. Never `providers` directly.
6. `workers` imports `services`, `domain`, `db`, `providers`. Never `api`, `review`.
7. `paddleocr`, `cv2`, `numpy` and `pypdfium2` may only be imported under `services/conditioning.py`,
   `services/readers/paddle.py`, `services/page_model.py` and `workers/` handlers.
8. No module imports a global database engine, provider client or storage client. Dependencies are
   injected or resolved from a factory at the process boundary.

The API image is built from the same source but installs only the base dependencies. A test asserts
that importing `trade_extract.api.app` does not import `paddleocr`, `cv2`, `numpy` or `pypdfium2`.

## 4. Naming conventions

| Thing | Convention | Example |
|---|---|---|
| Python packages | `snake_case` | `field_merge.py` |
| Classes | `PascalCase` | `FieldCandidate` |
| Database tables | `snake_case`, plural | `field_values` |
| IDs | prefix + `uuid4().hex` | `doc_9f2c…` |
| Document types | `snake_case` | `commercial_invoice` |
| Canonical keys | dotted, singular | `shipment.gross_weight_kg` |
| Field paths | dotted, array indices in brackets | `line_items[0].quantity` |
| Check codes | uppercase, hyphenated | `INV-003` |
| Task kinds | verb + noun | `extract_segment` |
| Error codes | `snake_case` | `file_too_large` |
| Policy versions | monotonic integer | `3` |
| Schema versions | type + semantic version | `invoice.peppol-1.0` |

## 5. Build order vs file structure

The tree is the destination, not the first commit. Build in the order in
[`07-implementation-blueprint.md`](07-implementation-blueprint.md):

1. Config, logging, DB session, migrations, health.
2. Clients, API keys, jobs, submissions, files, upload validation, queue.
3. Pages, conditioning, segmentation.
4. Envelope, schemas, provenance.
5. Provider adapter and text/vision readers.
6. PaddleOCR, routing, tables.
7. Invoice extraction, checks, decision.
8. Conflicts, accumulation.
9. Review UI, resolutions, trust.
10. Remaining document types.
11. Retention, webhooks, exports, hardening.

## 6. What does not get a folder

- No `utils/` dumping ground. Helpers live with the domain concept they serve.
- No `models/` at the top level. Database models are `db/models`; document schemas are `schemas`.
- No `tests/fixtures` in production packages. Fixtures are test-only.
- No provider SDK calls outside `providers/`.
- No SQL outside `db/repositories/` and migrations, except the documented queue claim query, which
  lives in `services/queue.py` because it is the queue contract.
