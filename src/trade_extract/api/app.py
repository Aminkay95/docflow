from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from trade_extract.api import openapi as openapi_module
from trade_extract.api.errors import register_exception_handlers
from trade_extract.api.middleware import RequestContextMiddleware
from trade_extract.api.routes import extract, health, results
from trade_extract.config import Settings, get_settings
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.clock import Clock, SystemClock
from trade_extract.logging import configure_logging
from trade_extract.services.storage import build_storage


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    engine = make_engine(settings)
    factory = session_factory(engine)
    storage = build_storage(settings)
    process_clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="Trade Extract API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.storage = storage
    app.state.clock = process_clock

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router, prefix="/v1")
    app.include_router(extract.router, prefix="/v1")
    app.include_router(results.router, prefix="/v1")

    app.openapi = lambda: openapi_module.custom_openapi(app)  # type: ignore[method-assign]
    return app
