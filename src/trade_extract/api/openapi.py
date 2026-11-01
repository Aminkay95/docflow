from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

_TAGS = [
    {"name": "Extraction", "description": "Submit trade documents for extraction."},
    {"name": "Results", "description": "Poll a submission result."},
    {"name": "Health", "description": "Liveness and readiness. No authentication."},
]


def custom_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title="Trade Extract API",
        version="1.0.0",
        description=(
            "Provenance-first asynchronous extraction API for Kenyan trade documents.\n\n"
            "A caller submits a group of files under an optional opaque `job_ref`. "
            "Authentication is a bearer API key. Completion is delivered by webhook "
            "with polling as a fallback. Every error uses the same envelope."
        ),
        routes=app.routes,
        tags=_TAGS,
        servers=[
            {"url": "https://api.trade-extract.example", "description": "Production"},
            {"url": "http://localhost:8000", "description": "Local development"},
        ],
    )
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "API key",
        "description": "`Authorization: Bearer tx_live_…`",
    }
    schema["security"] = [{"bearerAuth": []}]
    paths = schema.get("paths", {})

    components["schemas"]["Error"] = {
        "type": "object",
        "required": ["code", "message", "request_id"],
        "properties": {
            "code": {"type": "string", "examples": ["file_too_large"]},
            "message": {"type": "string"},
            "request_id": {"type": "string"},
            "details": {"type": "object", "additionalProperties": True},
            "doc_url": {"type": "string", "format": "uri"},
        },
    }
    components["schemas"]["ErrorEnvelope"] = {
        "type": "object",
        "required": ["error"],
        "properties": {"error": {"$ref": "#/components/schemas/Error"}},
    }

    for operations in paths.values():
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            for status_code, response in operation.get("responses", {}).items():
                if status_code.isdigit() and int(status_code) >= 400:
                    response["content"] = {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                        }
                    }

    paths = schema.get("paths", {})
    extract = paths.get("/v1/extract", {}).get("post")
    if extract is not None:
        settings = getattr(app.state, "settings", None)
        max_documents = getattr(settings, "max_documents_per_group", 20)
        max_job_ref = getattr(settings, "max_job_ref_length", 128)
        min_idempotency = getattr(settings, "min_idempotency_key_length", 8)
        max_idempotency = getattr(settings, "max_idempotency_key_length", 128)
        max_webhook_url = getattr(settings, "max_webhook_url_length", 2048)
        extract["parameters"] = [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": False,
                "schema": {
                    "type": "string",
                    "minLength": min_idempotency,
                    "maxLength": max_idempotency,
                },
                "description": (
                    "Same key + same body returns the original result; a different body "
                    "returns 409."
                ),
            }
        ]
        extract["requestBody"] = {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["documents"],
                        "properties": {
                            "job_ref": {
                                "type": "string",
                                "maxLength": max_job_ref,
                                "description": (
                                    "Opaque caller reference. Same value accumulates into one job."
                                ),
                            },
                            "webhook_url": {
                                "type": "string",
                                "format": "uri",
                                "maxLength": max_webhook_url,
                                "description": (
                                    "Per-submission webhook override. HTTPS in production."
                                ),
                            },
                            "documents": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": max_documents,
                                "items": {"type": "string", "format": "binary"},
                            },
                        },
                    },
                    "encoding": {
                        "documents": {
                            "contentType": (
                                "application/pdf, image/jpeg, image/png, image/webp, image/tiff"
                            )
                        }
                    },
                }
            },
        }

    results = paths.get("/v1/results/{result_id}", {}).get("get")
    if results is not None:
        from trade_extract.api.routes.results import INCLUDE_VALUES

        values = "|".join(sorted(INCLUDE_VALUES))
        pattern = f"^(?:(?:{values})(?:,(?:{values}))*)?$"
        for parameter in results.get("parameters", []):
            if parameter.get("name") == "include":
                parameter["description"] = (
                    "Comma list drawn from: " + ", ".join(sorted(INCLUDE_VALUES)) + "."
                )
                parameter["schema"] = {
                    "type": "string",
                    "default": "documents,conflicts,decision",
                    "pattern": pattern,
                }

    for health_path in ("/v1/health", "/v1/health/ready"):
        operation = paths.get(health_path, {}).get("get")
        if operation is not None:
            operation["security"] = []

    app.openapi_schema = schema
    return schema
