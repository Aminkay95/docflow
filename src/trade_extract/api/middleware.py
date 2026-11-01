from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from trade_extract.domain.ids import new_id
from trade_extract.logging import get_logger

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
logger = get_logger("trade_extract.api")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, emit a content-free access log, and echo the id."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        supplied = request.headers.get("X-Request-Id", "")
        request_id = supplied if _REQUEST_ID.match(supplied) else new_id("req_")
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_crashed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
