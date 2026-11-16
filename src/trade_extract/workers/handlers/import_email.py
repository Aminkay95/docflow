"""Download and submit attachments after the webhook has been acknowledged."""

import httpx
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings
from trade_extract.db.models.task import JobTask
from trade_extract.domain.clock import Clock
from trade_extract.services.email_intake import import_message
from trade_extract.services.storage import build_storage


def handle(session: Session, task: JobTask, *, settings: Settings, clock: Clock) -> None:
    factory = sessionmaker(bind=session.get_bind())
    with httpx.Client(timeout=settings.resend_timeout_seconds) as http:
        import_message(settings, factory, build_storage(settings), clock, http, task.payload)
