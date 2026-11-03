from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings
from trade_extract.db.models.job import Job
from trade_extract.db.models.task import JobTask
from trade_extract.domain.clock import Clock, SystemClock
from trade_extract.domain.errors import DomainError
from trade_extract.logging import configure_logging, get_logger
from trade_extract.services import queue as queue_service
from trade_extract.workers.handlers import extract_segment, finalize_submission, prepare_file

logger = get_logger("trade_extract.worker")

Handler = Callable[..., None]

HANDLERS: dict[str, Handler] = {
    "prepare_file": prepare_file.handle,
    "extract_segment": extract_segment.handle,
    "finalize_submission": finalize_submission.handle,
}

DEFAULT_KINDS = ["prepare_file", "extract_segment", "finalize_submission"]


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_once(
    *,
    settings: Settings,
    session_factory_: sessionmaker[Session],
    clock: Clock,
    worker_id: str,
    kinds: list[str] | None = None,
    handlers: dict[str, Handler] | None = None,
) -> bool:
    """Claim and process at most one task. Returns True when a task was processed."""

    handler_map = handlers or HANDLERS
    kinds = kinds or DEFAULT_KINDS

    with session_factory_() as session:
        task = queue_service.claim_task(session, worker_id, kinds, clock.now())
        session.commit()

    if task is None:
        return False

    task_id = task.id
    job_id = task.job_id
    claimed_owner, claimed_attempt = task.locked_by, task.attempts
    try:
        with session_factory_() as session:
            claimed = session.get(JobTask, task_id)
            if claimed is None:
                return True
            handler = handler_map[claimed.kind]
            handler(session, claimed, settings=settings, clock=clock)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - task failures are recorded, not raised
        error_code = exc.code if isinstance(exc, DomainError) else type(exc).__name__
        error_message = str(exc) if str(exc) else type(exc).__name__
        with session_factory_() as session:
            session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            claimed = session.scalar(select(JobTask).where(JobTask.id == task_id).with_for_update())
            if (
                claimed is not None
                and claimed.status == "running"
                and claimed.locked_by == claimed_owner
                and claimed.attempts == claimed_attempt
            ):
                queue_service.mark_failed(
                    session,
                    claimed,
                    now=clock.now(),
                    error_code=error_code,
                    error=error_message,
                    retry=True,
                    backoff_base=settings.task_backoff_base_seconds,
                    backoff_max=settings.task_backoff_max_seconds,
                )
                if claimed.status == "dead":
                    queue_service.terminalise_dead_task(
                        session,
                        claimed,
                        settings=settings,
                        now=clock.now(),
                        code=error_code,
                        message=error_message,
                    )
                session.commit()
        logger.warning(
            "task_failed",
            extra={"task_id": task_id, "error_code": error_code},
        )
        return True

    with session_factory_() as session:
        claimed = session.get(JobTask, task_id)
        if (
            claimed is not None
            and claimed.status == "running"
            and claimed.locked_by == claimed_owner
            and claimed.attempts == claimed_attempt
        ):
            queue_service.mark_succeeded(session, claimed, now=clock.now())
            session.commit()
        elif claimed is not None:
            # A handler may deliberately re-arm itself (finalize while siblings run).
            session.commit()
    return True


def run_forever(
    *,
    settings: Settings,
    session_factory_: sessionmaker[Session],
    clock: Clock | None = None,
    worker_id: str | None = None,
    kinds: list[str] | None = None,
) -> None:
    configure_logging()
    clock = clock or SystemClock()
    worker_id = worker_id or settings.worker_id or default_worker_id()
    kinds = kinds or DEFAULT_KINDS
    logger.info("worker_started", extra={"worker_id": worker_id, "kinds": kinds})
    while True:
        try:
            processed = run_once(
                settings=settings,
                session_factory_=session_factory_,
                clock=clock,
                worker_id=worker_id,
                kinds=kinds,
            )
        except KeyboardInterrupt:
            logger.info("worker_stopped", extra={"worker_id": worker_id})
            return
        except Exception:  # noqa: BLE001 - a transient claim error must not stop the loop
            logger.exception("worker_loop_error", extra={"worker_id": worker_id})
            processed = False
        if not processed:
            time.sleep(settings.worker_poll_seconds)
