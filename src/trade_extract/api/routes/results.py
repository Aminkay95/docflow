from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from trade_extract.api.deps import get_session, get_settings, require_scope
from trade_extract.api.schemas.results import Result
from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.repositories import submissions as submissions_repo
from trade_extract.domain import errors
from trade_extract.domain.hashing import sha256_bytes, stable_json
from trade_extract.services import ingest

router = APIRouter(tags=["Results"])

INCLUDE_VALUES = {"documents", "conflicts", "checks", "provenance", "review", "decision"}


def _etag(result: Result) -> str:
    body = stable_json(result.model_dump(mode="json"))
    return '"' + sha256_bytes(body.encode("utf-8"))[:32] + '"'


def _matches_etag(header: str, etag: str) -> bool:
    """List-aware, weak-aware ``If-None-Match`` comparison."""

    for candidate in header.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
    return False


def _parse_include(value: str) -> set[str]:
    requested = {part.strip() for part in value.split(",") if part.strip()}
    unknown = sorted(requested - INCLUDE_VALUES)
    if unknown:
        raise errors.invalid_request(
            "include",
            "must be a comma list drawn from: " + ", ".join(sorted(INCLUDE_VALUES)),
        )
    return requested if requested else set(INCLUDE_VALUES)


@router.get(
    "/results/{result_id}",
    response_model=Result,
    responses={
        304: {"description": "Not modified."},
        401: {"description": "Missing, malformed, revoked or expired key."},
        403: {"description": "Key lacks the results:read scope."},
        404: {"description": "Unknown result id for this account."},
        500: {"description": "Unhandled server error."},
        503: {"description": "Database, storage or maintenance outage."},
    },
)
def get_result(
    request: Request,
    result_id: str,
    response: Response,
    include: str = Query(default="documents,conflicts,decision"),
    document_id: str | None = Query(default=None),
    client: Client = Depends(require_scope("results:read")),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Result | Response:
    included = _parse_include(include)
    submission = submissions_repo.get_by_result_id(session, result_id, client.id)
    if submission is None:
        raise errors.result_not_found()

    payload = ingest.build_result(
        session,
        submission,
        include=included,
        document_id=document_id,
    )
    result = Result.model_validate(payload)
    etag = _etag(result)

    if_none_match = request.headers.get("If-None-Match")
    if if_none_match and _matches_etag(if_none_match, etag):
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": "no-store",
                "X-Request-Id": getattr(request.state, "request_id", ""),
            },
        )

    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = etag
    if result.status in ("queued", "processing"):
        response.headers["Retry-After"] = str(settings.retry_after_seconds)
    return result
