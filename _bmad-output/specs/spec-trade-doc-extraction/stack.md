# Stack

Implementation prescription. The spec says *what* to build; this says *what with*. Deviating is allowed — deviating silently is not.

## Shape

One language, four processes, one stateful dependency.

| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| API | FastAPI |
| Validation and schemas | Pydantic v2 |
| Jobs | PostgreSQL `SKIP LOCKED` polling worker |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| Object storage | S3-compatible (Cloudflare R2 or AWS S3) in production; filesystem-backed adapter in development |
| PDF rasterising | pypdfium2 |
| Image conditioning | Pillow + OpenCV (headless) |
| OCR and tables | PaddleOCR (PP-Structure) |
| Vision and LLM | DeepSeek via an internal provider adapter |
| Review UI | Jinja2 + HTMX, served by the API |
| Packaging | uv |
| Lint and format | Ruff |
| Types | mypy or pyright, strict on domain modules |
| Tests | pytest + pytest-asyncio |
| Tracing | structured JSON logs, every model call recorded to Postgres |

## Why Python, and not a polyglot split

PaddleOCR is Python. That single fact decides the language. The alternatives are (a) one Python codebase, or (b) a TypeScript API calling a Python OCR service over HTTP. Option (b) buys a nicer API ergonomics story and costs a second language, a second dependency tree, a second deploy path and an internal RPC contract — for a small team, that is a bad trade.

PaddleOCR is confined to a worker image, so the API image stays slim and the heavy dependency stays in the one place that needs it. That is the isolation option (b) would have bought, without the second language.

## Why FastAPI and Pydantic

The spec's central artefact is seven output schemas that must be validated on every response and repaired on mismatch. Pydantic v2 generates JSON Schema from the same models used for validation, so the schema exists once and is used three ways: as the model's output contract, as the validator, and as published API documentation. FastAPI also emits OpenAPI for free, which matters for a developer-facing product.

## Why Postgres-as-queue instead of Celery and Redis

A job is durable, takes minutes, and must survive a restart. Celery works, but it adds Redis, a second datastore to back up, and a configuration surface that earns nothing at this volume. `SELECT ... FOR UPDATE SKIP LOCKED` over a `jobs` table gives durable claiming, retries, visibility timeouts and dead-lettering in a few hundred lines, with one datastore to operate and one transaction boundary that covers both the job and its result.

Revisit when throughput or fan-out justifies it. Not before.

## Why HTMX instead of a SPA

The review queue is an internal operations tool used by a handful of reviewers on desktop. Server-rendered fragments are faster to build, need no build step, no client state management and no second deployment. If the product later grows a customer-facing portal, that is a different application with a different framework, and this decision does not block it.

## Containerisation

Every service runs under Docker Compose, and `docker compose up` brings the whole stack from a clean clone with no host prerequisites beyond Docker itself.

Services:

- `api` — FastAPI, serves the JSON API, webhooks and the review UI
- `worker` — job processor; the only image containing PaddleOCR, OpenCV and pypdfium2
- `postgres` — datastore and job queue
- `web` — optional, only if the review UI is later split out

Rules:

- The API image must not contain PaddleOCR or OpenCV. If it does, the split has been lost.
- Development uses the same images as production, with only environment and secrets differing. No `docker-compose.override.yml` that changes what is installed.
- CPU is the default execution path so the stack runs on any laptop. GPU is an opt-in Compose profile, never a requirement to boot.
- The PaddleOCR image is large. Accept it, pin it, and do not let it creep into other services.
- Healthchecks on every service; `api` depends on `postgres` being healthy, not merely started.
- Secrets arrive by environment. No secrets in images, no secrets in Compose files committed to the repository.
- Data volume for Postgres is a named volume, and `docker compose down` must never destroy it by default.

## Storage, and why not MinIO

Files do not belong in Postgres. A 20-document group at the 25 MB limit is up to 500 MB of source, and every rasterised page adds a few more megabytes; bolting that into the database bloats the write-ahead log, inflates every backup, and gives you no streaming or range requests.

Object storage is the answer, but **not MinIO**. The MinIO repository was archived in April 2026 and is no longer maintained; the community edition is now source-only distribution with no precompiled binaries, and its AGPLv3 terms put the compliance burden on the operator. Starting a new project on it in 2026 is a bad bet.

The resolution is a storage interface with four operations — put, get, delete, presigned URL — and two implementations:

- **Production:** any S3-compatible service. Cloudflare R2 is the default recommendation: S3-compatible API, no egress fees, and a free tier that covers early volume. AWS S3 if the client contract demands it.
- **Development:** a filesystem-backed adapter with the same interface. No extra container, no credentials, and `docker compose up` stays fast from a clean clone.

If real S3 semantics are wanted locally, SeaweedFS is an Apache-2.0 option and can be added as an optional Compose profile. It is not required to boot the stack.

The interface is the point: swapping the backend is configuration, not a code change and not a different image.

## Retention

Documents are kept for **six hours after the job reaches a terminal state**, then purged. Re-extraction is a development concern; in production, needing to re-extract means the job was not finished. This is a privacy posture and it should be sold as one.

"Terminal" is the load-bearing word. It means posted, or review-resolved. A job sitting in the review queue, or carrying an unresolved conflict, has not reached terminal state and its clock has not started — otherwise the six-hour window can expire before a reviewer ever opens it, and the queue would be reviewing documents it can no longer display.

Three consequences that are easy to get wrong:

1. **The purge does not purge the data.** The extraction record — stated values, provenance coordinates, document hash — is what answers a dispute months later, so it outlives the file. It is retained for **the life of the account plus ninety days after termination**, exportable in full on request, and deleted on request before expiry. Exportability is what makes the retention defensible: the customer can always take their data with them.
2. **S3 versioning will silently defeat the purge.** A versioned bucket keeps every "deleted" object as a non-current version. Expire non-current versions on the same schedule, or the purge is a fiction. The same applies to any snapshot of the storage volume.
3. **A purge that fails quietly is worse than a long policy**, because nobody expects it. Emit a metric for objects older than policy, and alert on it. The retention promise is only as good as the sweeper's visibility.

**Telemetry is the retention policy nobody writes down.** The most common way a six-hour promise becomes a ninety-day reality is observability: an error report attaching the failing page, a trace store keeping full prompt payloads, a debug line with a base64 blob. The rule is absolute — logs, traces, error reports and metrics carry hashes, model versions, timings, token counts and field outcomes, never page images, never document text, never prompt payloads. Provider-side tracing, if ever wanted for a specific investigation, is a deliberate time-boxed exception with its own deletion date.

Backups run on their own clock and quietly extend the real deletion date. State the effective date including backups, or do not state one.

Provenance stays portable regardless: page index, bounding box and document hash travel in the output, so a client can render the source overlay against their own copy once the platform has purged its own.

## Deliberate omissions from v1

No Kubernetes, no message broker, no Redis, no LangChain, no vector database, no prompt-templating framework, no second language. Each is a defensible thing to add later and none of them is earned yet.

## Traps worth recording

- **Do not use PyMuPDF.** It is AGPL, which is incompatible with a commercial hosted service. Use pypdfium2, which is permissively licensed and fast.
- **Do not use MinIO.** Repository archived April 2026, no longer maintained, source-only distribution, AGPLv3. Use R2/S3 in production and the filesystem adapter in development.
- **Prompts must be byte-stable per document type.** DeepSeek prices cache hits at roughly one twentieth of cache misses; a prompt with a timestamp or a shuffled field order destroys that saving.
- **Provider JSON mode is not schema adherence.** The validate-and-repair loop is not optional hardening, it is part of the pipeline.
- **`deepseek-v4-pro` cannot read images.** Only `deepseek-flash` may be wired as the vision reader.
