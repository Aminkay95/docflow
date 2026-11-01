from __future__ import annotations

import time
from datetime import timedelta

import httpx
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.job import Job
from trade_extract.db.models.webhook import WebhookDelivery
from trade_extract.db.session import make_engine, session_factory
from trade_extract.domain.clock import Clock, SystemClock
from trade_extract.logging import configure_logging, get_logger
from trade_extract.services import queue as queue_service
from trade_extract.services import webhooks as webhook_service

logger = get_logger("trade_extract.maintenance")


def run_webhook_once(
    *,
    settings: Settings,
    session_factory_: sessionmaker[Session],
    clock: Clock,
    http_client: httpx.Client | None = None,
) -> bool:
    now = clock.now()
    with session_factory_() as session:
        delivery = webhook_service.claim_due_delivery(
            session, now, lease_seconds=settings.webhook_stale_after_seconds
        )
        session.commit()

    if delivery is None:
        return False

    delivery_id = delivery.id
    with session_factory_() as session:
        claimed = session.get(WebhookDelivery, delivery_id)
        if claimed is None:
            return True
        job = session.get(Job, claimed.job_id)
        client = session.get(Client, job.client_id) if job is not None else None
        if client is None:
            claimed.status = "dead"
            claimed.last_error = "client no longer exists"
            session.commit()
            return True
        outcome = webhook_service.deliver_once(
            session,
            claimed,
            client,
            settings=settings,
            clock=clock,
            http_client=http_client,
        )
        session.commit()

    logger.info(
        "webhook_attempt",
        extra={
            "delivery_id": outcome.delivery_id,
            "outcome": outcome.outcome,
            "status_code": outcome.status_code,
        },
    )
    return True


def requeue_stale_tasks_once(
    *,
    settings: Settings,
    session_factory_: sessionmaker[Session],
    clock: Clock,
) -> int:
    now = clock.now()
    older_than = now - timedelta(seconds=settings.task_stale_after_seconds)
    with session_factory_() as session:
        outcome = queue_service.requeue_stale(
            session,
            older_than=older_than,
            now=now,
            max_requeues=settings.task_max_requeues,
        )
        for task in outcome.dead:
            queue_service.terminalise_dead_task(
                session,
                task,
                settings=settings,
                now=now,
                code=queue_service.STALE_REQUEUE_LIMIT_CODE,
                message=(
                    f"Task '{task.kind}' was crash-requeued {task.requeues} times; "
                    f"the limit is {settings.task_max_requeues}."
                ),
            )
        session.commit()
    if outcome.requeued:
        logger.warning("tasks_requeued", extra={"count": outcome.requeued})
    if outcome.dead:
        logger.warning("tasks_dead_after_requeue_limit", extra={"count": len(outcome.dead)})
    return outcome.requeued


def reclaim_stale_deliveries_once(
    *,
    session_factory_: sessionmaker[Session],
    clock: Clock,
    settings: Settings | None = None,
) -> int:
    now = clock.now()
    with session_factory_() as session:
        count = webhook_service.reclaim_stale_deliveries(
            session,
            now=now,
            max_attempts=settings.webhook_max_attempts if settings is not None else None,
        )
        session.commit()
    if count:
        logger.warning("webhook_deliveries_reclaimed", extra={"count": count})
    return count


def run_forever(
    *,
    settings: Settings,
    session_factory_: sessionmaker[Session],
    clock: Clock | None = None,
) -> None:
    configure_logging()
    clock = clock or SystemClock()
    logger.info("maintenance_started")
    while True:
        try:
            processed = run_webhook_once(
                settings=settings,
                session_factory_=session_factory_,
                clock=clock,
            )
            requeue_stale_tasks_once(
                settings=settings,
                session_factory_=session_factory_,
                clock=clock,
            )
            reclaim_stale_deliveries_once(
                session_factory_=session_factory_,
                clock=clock,
                settings=settings,
            )
        except KeyboardInterrupt:
            logger.info("maintenance_stopped")
            return
        except Exception:  # noqa: BLE001 - transient errors must not stop the loop
            logger.exception("maintenance_loop_error")
            processed = False
        if not processed:
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    from trade_extract.config import get_settings

    settings = get_settings()
    engine = make_engine(settings)
    run_forever(
        settings=settings,
        session_factory_=session_factory(engine),
    )


if __name__ == "__main__":
    main()
