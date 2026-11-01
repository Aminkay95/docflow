from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from trade_extract import __version__
from trade_extract.api.deps import get_session, get_settings, get_storage
from trade_extract.config import Settings
from trade_extract.db.repositories import policies as policies_repo
from trade_extract.domain import errors
from trade_extract.services.storage import FilesystemStorage, ObjectStorage

router = APIRouter(tags=["Health"])


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "version": __version__}


@router.get(
    "/health/ready",
    responses={503: {"description": "Database, storage or active policy unavailable."}},
)
def ready(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorage = Depends(get_storage),
) -> dict[str, object]:
    checks: dict[str, str] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = "unavailable"
        raise errors.service_unavailable("database unavailable") from exc

    if isinstance(storage, FilesystemStorage):
        checks["storage"] = "ok" if storage.root.exists() else "unavailable"
    else:
        checks["storage"] = "ok"
    if checks["storage"] != "ok":
        raise errors.service_unavailable("object storage unavailable")

    if policies_repo.get_active(session) is None:
        checks["policy"] = "unavailable"
        raise errors.service_unavailable("no active policy")

    checks["policy"] = "ok"
    return {"status": "ok", "checks": checks, "version": __version__}
