# Trade Extract

Provenance-first asynchronous extraction API for Kenyan trade documents.

## Local development

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --extra dev
uv run alembic upgrade head
uv run python scripts/bootstrap.py
uv run python scripts/create_client.py --name "Developer" --slug developer
uv run uvicorn trade_extract.api.app:create_app --factory --reload
```

The API is then available at `http://localhost:8000`, with liveness at
`GET /v1/health` and readiness at `GET /v1/health/ready`.

Provision an internal reviewer with
`uv run python scripts/provision_reviewer.py USERNAME --email USER@EXAMPLE.COM`
(the password is prompted), then open `/review/login`. Customer API keys cannot resolve
review items. For local development only, `uv run python scripts/seed_admin.py` seeds an
`admin` account (`admin@example.com`, password `password123`; the script refuses to run
when `ENVIRONMENT=production` unless `--force` is passed).

"Forgot password?" on the login page emails a single-use reset link that expires after
30 minutes. Resend is the primary transport: set `RESEND_API_KEY` and `RESEND_FROM` (an
address on a domain verified in Resend). Without Resend, `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` deliver mail over SMTP. With neither
configured the API writes the message to `MAIL_OUTBOX_DIR` (`./var/outbox` by default),
which is the local-development path. Resetting revokes every active session for that
reviewer.

The dashboard links to separate extraction and conflict queues. Corrections
are labelled overlays; original extraction values remain available in results.

Repeated submissions with the same client and `job_ref` share one job. Follow the returned
`job_url` for accumulated documents and conflicts; each `status_url` remains scoped to its
submission. All seven registered types extract; the six noncommercial types always require
full review. Existing schema versions and local field paths are preserved.

## Extraction workers

Set `PROVIDER=deepseek` or `PROVIDER=local` in `.env` and recreate the
worker to select the model backend:

```bash
docker compose up -d --no-deps worker
```

DeepSeek uses the configured API key. For Qwen, place the
[Qwen3.5-4B Q4_K_M GGUF and its F16 vision projector](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/tree/main)
in `./var/models` or set `LOCAL_MODEL_DIR` to their directory. Set
`PROVIDER=local` and wait for the CPU model service before starting the stack:

```bash
docker compose up -d --wait model
docker compose up -d
```

The worker reaches Qwen at `http://model:8080/v1` within Compose.
Remove any `LOCAL_MODEL_BASE_URL` override pointing to a host-run server before
recreating the worker.
To switch to DeepSeek, set `PROVIDER=deepseek`, recreate the worker as above,
and use `docker compose stop model` to release the model's CPU and memory.

Docker Compose starts ten extraction workers by default. Each worker processes one
task at a time, so up to ten documents can be extracted concurrently when extraction
tasks are ready. Set `EXTRACTION_WORKER_REPLICAS` in `.env` to adjust for available
CPU and memory. Apply the configured count to a running Compose stack with
`docker compose up -d worker`.

## Inbound PDF email

Set `RESEND_API_KEY` to a key with receiving access, `EMAIL_INTAKE_ADDRESS` to
the pilot address on a Resend receiving domain, and `EMAIL_INTAKE_CLIENT_SLUG`
to the active Trade Extract client that owns that address.

In Resend, register the public HTTPS URL ending in
`/v1/webhooks/resend/inbound` for the `email.received` event. Save its signing
secret as `EMAIL_INTAKE_WEBHOOK_SECRET`, apply the database migrations, and
restart the API and workers. Each distinct PDF sent to the configured address
as the sole To recipient creates a Jobs entry. Resend retries and event replays
reuse the original attachment identity.

With the signing secret unset, the API continues to check the inbox every
minute by default. Setting it enables webhook delivery and disables inbox
polling. Verify delivery with a test PDF and confirm that replaying the event
does not create another job.

[Resend receiving domain setup](https://resend.com/docs/dashboard/receiving/custom-domains)

## Tests

Node.js is required for the frontend behavioral checks.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/trade_extract/domain src/trade_extract/services
uv run pytest -q
node --test tests/review_upload.test.cjs
docker compose up -d postgres
uv run pytest -q -m integration
```
