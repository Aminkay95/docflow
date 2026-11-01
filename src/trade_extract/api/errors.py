from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from trade_extract.domain import errors
from trade_extract.domain.errors import DomainError
from trade_extract.domain.ids import new_id

logger = logging.getLogger(__name__)


def request_id_for(request: Request) -> str:
    return getattr(request.state, "request_id", "") or new_id("req_")


def error_response_payload(error: DomainError, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": request_id,
            "details": error.details,
            "doc_url": error.doc_url,
        }
    }


def _json(
    error: DomainError, request_id: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    merged: dict[str, str] = {"X-Request-Id": request_id, "Cache-Control": "no-store"}
    if headers:
        merged.update(dict(headers))
    return JSONResponse(
        status_code=error.http_status,
        content=error_response_payload(error, request_id),
        headers=merged,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        request_id = request_id_for(request)
        if exc.http_status >= 500:
            logger.error(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "error_code": exc.code,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
        return _json(exc, request_id)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = request_id_for(request)
        details = {
            "errors": [
                {
                    "location": [str(part) for part in error.get("loc", ())],
                    "message": str(error.get("msg", "invalid value")),
                }
                for error in exc.errors()
            ]
        }
        return _json(
            DomainError(
                "invalid_request",
                "The request parameters are invalid. Correct the named fields and resubmit.",
                http_status=400,
                details=details,
            ),
            request_id,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = request_id_for(request)
        if exc.status_code == 404:
            error = DomainError(
                "endpoint_not_found",
                f"No endpoint is served at {request.method} {request.url.path}. "
                "Use a documented /v1 path.",
                http_status=404,
                details={"method": request.method, "path": request.url.path},
            )
        elif exc.status_code == 405:
            error = DomainError(
                "invalid_request",
                "This HTTP method is not supported for this path. Use a documented method.",
                http_status=405,
            )
        elif exc.status_code == 406:
            error = DomainError(
                "invalid_request",
                "The requested content type is not supported. Use Accept: application/json.",
                http_status=406,
            )
        else:
            error = DomainError(
                "invalid_request",
                "The request could not be processed. Check the path and parameters.",
                http_status=exc.status_code,
            )
        return _json(error, request_id, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = request_id_for(request)
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        del exc
        return _json(errors.internal_error(), request_id)
