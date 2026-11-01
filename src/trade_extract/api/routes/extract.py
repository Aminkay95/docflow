from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from trade_extract.api.deps import get_clock, get_session, get_settings, get_storage, require_scope
from trade_extract.api.schemas.extract import ExtractAccepted
from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.domain.clock import Clock
from trade_extract.services import ingest
from trade_extract.services.limits import (
    spool_submission,
    validate_idempotency_key,
    validate_submission,
)
from trade_extract.services.storage import ObjectStorage

router = APIRouter(tags=["Extraction"])


@router.post(
    "/extract",
    status_code=202,
    response_model=ExtractAccepted,
    responses={
        400: {"description": "Malformed or invalid request."},
        401: {"description": "Missing, malformed, revoked or expired key."},
        403: {"description": "Key lacks the extract:write scope."},
        409: {"description": "Idempotency or job state conflict."},
        413: {"description": "A file or the request body exceeds a configured cap."},
        415: {"description": "Magic bytes are not an accepted document type."},
        422: {"description": "A named limit or document rule was broken."},
        500: {"description": "Unhandled server error."},
        503: {"description": "Database, storage or maintenance outage."},
    },
)
async def extract_documents(
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    client: Client = Depends(require_scope("extract:write")),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorage = Depends(get_storage),
    clock: Clock = Depends(get_clock),
) -> ExtractAccepted:
    # Auth and scope resolved before the body is read (dependencies run first).
    validated_key = validate_idempotency_key(idempotency_key, settings)

    raw = await spool_submission(request.stream(), request.headers.get("content-type"), settings)
    validated = None
    try:
        validated = await run_in_threadpool(validate_submission, raw, settings)
        accepted = await run_in_threadpool(
            ingest.accept,
            session,
            client=client,
            validated=validated,
            idempotency_key=validated_key,
            storage=storage,
            settings=settings,
            clock=clock,
        )
    finally:
        if validated is not None:
            validated.cleanup()
        else:
            raw.cleanup()

    response.headers["Location"] = f"/v1/results/{accepted.result_id}"
    response.headers["Retry-After"] = str(settings.retry_after_seconds)
    return ExtractAccepted(
        result_id=accepted.result_id,
        job_id=accepted.job_id,
        job_ref=accepted.job_ref,
        status=cast(Literal["queued", "processing", "completed", "failed"], accepted.status),
        submitted_documents=accepted.submitted_documents,
        duplicate_documents=accepted.duplicate_documents,
        status_url=accepted.status_url,
        created_at=accepted.created_at,
    )
