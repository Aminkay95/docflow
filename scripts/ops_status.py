"""Print content-free queue, provider and retention metrics from the private database."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from trade_extract.config import get_settings
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.provider_call import ProviderCall
from trade_extract.db.models.task import JobTask
from trade_extract.db.models.webhook import WebhookDelivery
from trade_extract.db.session import make_engine, session_factory


def main() -> None:
    settings = get_settings()
    engine = make_engine(settings)
    now = datetime.now(UTC)
    stale_after = now - timedelta(seconds=settings.task_stale_after_seconds)
    try:
        with session_factory(engine)() as session:
            tasks = session.execute(
                select(JobTask.kind, JobTask.status, func.count())
                .where(JobTask.status.in_(("queued", "running", "dead")))
                .group_by(JobTask.kind, JobTask.status)
                .order_by(JobTask.kind, JobTask.status)
            ).all()
            oldest = session.scalar(
                select(func.min(JobTask.run_after)).where(
                    JobTask.status == "queued", JobTask.run_after <= now
                )
            )
            active_extraction_workers, stale_extraction_tasks = session.execute(
                select(
                    func.count(func.distinct(JobTask.locked_by)).filter(
                        JobTask.locked_at > stale_after
                    ),
                    func.count().filter(JobTask.locked_at <= stale_after),
                ).where(
                    JobTask.status == "running",
                    JobTask.kind.in_(("prepare_file", "classify_segment", "extract_segment")),
                )
            ).one()
            attempts = session.execute(
                select(ProviderCall.status, func.count())
                .where(ProviderCall.created_at >= now - timedelta(hours=1))
                .group_by(ProviderCall.status)
            ).all()
            webhook_status = session.execute(
                select(WebhookDelivery.status, func.count())
                .where(WebhookDelivery.status.in_(("pending", "delivering", "failed", "dead")))
                .group_by(WebhookDelivery.status)
            ).all()
            retention_due = session.scalar(
                select(func.count())
                .select_from(File)
                .join(Job, Job.id == File.job_id)
                .where(
                    File.purged_at.is_(None),
                    File.status.in_(("processed", "failed")),
                    Job.terminal_at <= now - timedelta(hours=settings.document_retention_hours),
                )
            )
            review_jobs = session.scalar(
                select(func.count()).select_from(Job).where(Job.status == "review_required")
            )
    finally:
        engine.dispose()

    print(
        json.dumps(
            {
                "sampled_at": now.isoformat(),
                "tasks": [
                    {"kind": kind, "status": status, "count": count}
                    for kind, status, count in tasks
                ],
                "oldest_queue_seconds": int((now - oldest).total_seconds()) if oldest else 0,
                "active_extraction_workers": active_extraction_workers,
                "stale_extraction_tasks": stale_extraction_tasks,
                "provider_attempts_last_hour": dict(attempts),
                "webhooks": dict(webhook_status),
                "review_jobs": review_jobs,
                "files_due_for_retention": retention_due,
            }
        )
    )


if __name__ == "__main__":
    main()
