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
