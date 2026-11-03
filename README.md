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

Provision an internal reviewer with `uv run python scripts/provision_reviewer.py USERNAME`
(the password is prompted), then open `/review/login`. Customer API keys cannot resolve
review items. The dashboard links to separate extraction and conflict queues. Corrections
are labelled overlays; original extraction values remain available in results.

Repeated submissions with the same client and `job_ref` share one job. Follow the returned
`job_url` for accumulated documents and conflicts; each `status_url` remains scoped to its
submission. All seven registered types extract; the six noncommercial types always require
full review. Existing schema versions and local field paths are preserved.

## Tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/trade_extract/domain src/trade_extract/services
uv run pytest -q
docker compose up -d postgres
uv run pytest -q -m integration
```
