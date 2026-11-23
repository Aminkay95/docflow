"""Expire raw source material after a job reaches a terminal state."""

from datetime import datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.db.models.task import JobTask
from trade_extract.logging import get_logger
from trade_extract.services.storage import ObjectStorage

logger = get_logger("trade_extract.retention")


def purge_due_files(
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    *,
    now: datetime,
    retention_hours: float,
    batch_size: int = 20,
) -> int:
    cutoff = now - timedelta(hours=retention_hours)
    failed: set[str] = set()
    purged = 0
    for _ in range(batch_size):
        with factory() as session:
            file = session.scalar(
                select(File)
                .join(Job, (Job.id == File.job_id) & (Job.client_id == File.client_id))
                .where(
                    Job.terminal_at <= cutoff,
                    File.purged_at.is_(None),
                    File.status.in_(("processed", "failed")),
                    File.id.not_in(failed),
                    ~exists().where(
                        JobTask.job_id == Job.id, JobTask.status.in_(("queued", "running"))
                    ),
                )
                .order_by(Job.terminal_at, File.id)
                .limit(1)
                # Reopening a job takes the same lock before ingest writes any objects.
                .with_for_update(of=Job, skip_locked=True)
            )
            if file is None:
                break
            file_id = file.id
            try:
                # Delete versioned objects before marking them gone. Retry safely if the
                # storage call or database commit fails.
                prefix = f"clients/{file.client_id}/jobs/{file.job_id}/files/{file_id}/"
                storage.delete_prefix(prefix)
                session.execute(
                    update(Page)
                    .where(Page.file_id == file_id)
                    .values(
                        purged_at=now,
                        text_layer=None,
                        image_key=None,
                        thumbnail_key=None,
                    )
                )
                session.execute(
                    update(Document).where(Document.file_id == file_id).values(purged_at=now)
                )
                file.purged_at = now
                session.commit()
            except Exception:  # noqa: BLE001 - retry next sweep and keep processing other files
                session.rollback()
                failed.add(file_id)
                logger.error("retention_delete_failed", extra={"file_id": file_id})
            else:
                purged += 1
    return purged
