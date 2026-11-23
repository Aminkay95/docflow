"""Customer browser access. Tenant identity always comes from the session."""

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from trade_extract.api.deps import get_clock, get_session, get_settings, get_storage
from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.job import Job
from trade_extract.domain import errors
from trade_extract.domain.clock import Clock
from trade_extract.review.auth import tenant_client
from trade_extract.services import ingest
from trade_extract.services.jobs import read_document, read_job
from trade_extract.services.limits import (
    spool_submission,
    validate_idempotency_key,
    validate_submission,
)
from trade_extract.services.storage import ObjectStorage

router = APIRouter(prefix="/workspace", include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parents[2] / "review" / "templates")


def render(request: Request, client: Client, **context) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={
            "client_name": client.name,
            "csrf": request.state.review_session["csrf"],
            **context,
        },
    )


@router.get("")
def workspace(
    request: Request,
    before: str | None = Query(None, max_length=80),
    client: Client = Depends(tenant_client),
    session: Session = Depends(get_session),
) -> Response:
    listing = select(Job).where(Job.client_id == client.id)
    if before:
        previous = session.scalar(listing.where(Job.id == before))
        if previous is None:
            raise errors.job_not_found()
        listing = listing.where(tuple_(Job.created_at, Job.id) < (previous.created_at, previous.id))
    rows = list(session.scalars(listing.order_by(Job.created_at.desc(), Job.id.desc()).limit(26)))
    return render(
        request, client, jobs=rows[:25], next_page=rows[24].id if len(rows) > 25 else None
    )


@router.get("/jobs/{job_id}")
def job(
    job_id: str,
    request: Request,
    client: Client = Depends(tenant_client),
    session: Session = Depends(get_session),
) -> Response:
    return render(request, client, job=read_job(session, job_id, client.id))


@router.get("/documents/{document_id}")
def document(
    document_id: str,
    client: Client = Depends(tenant_client),
    session: Session = Depends(get_session),
) -> dict:
    return read_document(session, document_id, client.id)


@router.post("/jobs")
async def new_job(
    request: Request,
    client: Client = Depends(tenant_client),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorage = Depends(get_storage),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    idempotency = validate_idempotency_key(request.headers.get("Idempotency-Key"), settings)
    raw = await spool_submission(request.stream(), request.headers.get("content-type"), settings)
    validated = None
    try:
        validated = await run_in_threadpool(validate_submission, raw, settings)
        if validated.webhook_url:
            raise errors.invalid_request("webhook_url", "Workspace uploads use the client webhook.")
        accepted = await run_in_threadpool(
            ingest.accept,
            session,
            client=client,
            validated=validated,
            idempotency_key=idempotency,
            storage=storage,
            settings=settings,
            clock=clock,
        )
    finally:
        if validated is not None:
            validated.cleanup()
        else:
            raw.cleanup()
    return JSONResponse({"job_url": f"/workspace/jobs/{accepted.job_id}"}, status_code=201)
