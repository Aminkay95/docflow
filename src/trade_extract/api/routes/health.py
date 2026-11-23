from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from trade_extract import __version__
from trade_extract.api.deps import get_session, get_storage
from trade_extract.db.repositories import policies as policies_repo
from trade_extract.domain import errors
from trade_extract.services.storage import FilesystemStorage, ObjectStorage, S3Storage

router = APIRouter(tags=["Health"])

# Keep this aligned with the migration required by the deployed application.
REQUIRED_DATABASE_REVISION = "0009_tenant_workspace"


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "version": __version__}


@router.get(
    "/health/ready",
    responses={503: {"description": "Database schema, storage or active policy unavailable."}},
)
def ready(
    session: Session = Depends(get_session),
    storage: ObjectStorage = Depends(get_storage),
) -> dict[str, object]:
    checks: dict[str, str] = {}
    try:
        revisions = set(session.scalars(text("SELECT version_num FROM alembic_version")))
    except SQLAlchemyError as exc:
        raise errors.service_unavailable("database or migration metadata unavailable") from exc
    if revisions != {REQUIRED_DATABASE_REVISION}:
        raise errors.service_unavailable("database migration required")
    checks["database"] = "ok"

    if isinstance(storage, FilesystemStorage):
        checks["storage"] = "ok" if storage.root.exists() else "unavailable"
    else:
        if isinstance(storage, S3Storage):
            try:
                storage.check_ready()
            except Exception as exc:  # noqa: BLE001 - normalize SDK failures
                raise errors.service_unavailable("object storage unavailable") from exc
        checks["storage"] = "ok"
    if checks["storage"] != "ok":
        raise errors.service_unavailable("object storage unavailable")

    if policies_repo.get_active(session) is None:
        checks["policy"] = "unavailable"
        raise errors.service_unavailable("no active policy")

    checks["policy"] = "ok"
    return {"status": "ok", "checks": checks, "version": __version__}
