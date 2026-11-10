from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from trade_extract.api import openapi as openapi_module
from trade_extract.api.errors import register_exception_handlers
from trade_extract.api.middleware import RequestContextMiddleware
from trade_extract.api.routes import extract, health, jobs, results
from trade_extract.config import Settings, get_settings
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.clock import Clock, SystemClock
from trade_extract.logging import configure_logging
from trade_extract.review import routes as review_routes
from trade_extract.review.auth import signer
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
    app.state.review_login_attempts = defaultdict(deque)
    app.state.review_login_lock = Lock()
    app.state.review_reset_attempts = defaultdict(deque)
    app.state.review_reset_lock = Lock()

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router, prefix="/v1")
    app.include_router(extract.router, prefix="/v1")
    app.include_router(results.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")

    app.include_router(review_routes.router)
    app.mount(
        "/review/static",
        StaticFiles(directory=str(Path(__file__).parents[1] / "review" / "static")),
        name="review-static",
    )

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> RedirectResponse:
        try:
            signer(request).loads(request.cookies.get("review_session", ""), max_age=28800)
            target = "/review"
        except Exception:
            target = "/review/login"
        return RedirectResponse(target, status_code=303)

    @app.middleware("http")
    async def review_security(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/review"):
            response.headers.update(
                {
                    "Cache-Control": "no-store",
                    "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": (
                        "default-src 'self'; img-src 'self' data:; "
                        "style-src 'self'; script-src 'self'"
                    ),
                }
            )
            data = getattr(request.state, "review_session", None)
            if data and not request.url.path.endswith(("/logout", "/logout-all")):
                data["seen"] = process_clock.now().timestamp()
                response.set_cookie(
                    "review_session",
                    signer(request).dumps(data),
                    httponly=True,
                    secure=settings.environment == "production",
                    samesite="lax",
                    max_age=28800,
                )
        return response

    app.openapi = lambda: openapi_module.custom_openapi(app)  # type: ignore[method-assign]
    return app
