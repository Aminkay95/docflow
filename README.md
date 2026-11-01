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

## Tests

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src/trade_extract/domain src/trade_extract/services
uv run pytest -q
docker compose up -d postgres
uv run pytest -q -m integration
```
