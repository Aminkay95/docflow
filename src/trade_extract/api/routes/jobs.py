from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from trade_extract.api.deps import get_session, require_scope
from trade_extract.api.schemas.jobs import Job, PaginatedConflicts, PaginatedDocuments
from trade_extract.api.schemas.results import Document
from trade_extract.db.models.client import Client
from trade_extract.domain import errors
from trade_extract.services.jobs import read_collection, read_document, read_job

router = APIRouter(tags=["Jobs"])


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(
    job_id: str,
    response: Response,
    client: Client = Depends(require_scope("results:read")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return read_job(session, job_id, client.id)


@router.get("/jobs/{job_id}/documents", response_model=PaginatedDocuments)
@router.get("/jobs/{job_id}/conflicts", response_model=PaginatedConflicts)
def get_collection(
    job_id: str,
    request: Request,
    response: Response,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = None,
    status: str | None = None,
    document_type: str | None = None,
    client: Client = Depends(require_scope("results:read")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    collection = request.url.path.rsplit("/", 1)[-1]
    if collection not in ("documents", "conflicts"):
        raise errors.job_not_found()
    response.headers["Cache-Control"] = "no-store"
    return read_collection(
        session,
        job_id,
        client.id,
        collection=collection,
        limit=limit,
        cursor=cursor,
        status=status,
        document_type=document_type,
    )


@router.get("/documents/{document_id}", response_model=Document)
def get_document(
    document_id: str,
    response: Response,
    client: Client = Depends(require_scope("results:read")),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return read_document(session, document_id, client.id)
