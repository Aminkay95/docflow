"""Import PDF attachments for one configured Resend recipient."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from email.utils import parseaddr
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.repositories.submissions import get_by_idempotency_key
from trade_extract.domain.clock import Clock
from trade_extract.domain.errors import DomainError
from trade_extract.services.ingest import accept
from trade_extract.services.limits import (
    RawSubmission,
    SpooledDocument,
    detect_media_type,
    validate_submission,
)
from trade_extract.services.storage import ObjectStorage

logger = logging.getLogger(__name__)
_RECEIVING_URL = "https://api.resend.com/emails/receiving"


def import_inbox(
    settings: Settings,
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    clock: Clock,
    http: httpx.Client,
) -> int:
    headers = {"Authorization": f"Bearer {settings.resend_api_key.get_secret_value().strip()}"}
    params: dict[str, str] = {"limit": "100"}
    imported = 0
    while True:
        response = http.get(_RECEIVING_URL, params=params, headers=headers)
        response.raise_for_status()
        page = response.json()
        for message in page["data"]:
            imported += import_message(settings, factory, storage, clock, http, message)
        if not page["has_more"]:
            return imported
        params["after"] = page["data"][-1]["id"]


def import_message(
    settings: Settings,
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    clock: Clock,
    http: httpx.Client,
    message: dict,
) -> int:
    recipient = settings.email_intake_address.strip().casefold()
    if [parseaddr(value)[1].casefold() for value in message["to"]] != [recipient]:
        return 0
    headers = {"Authorization": f"Bearer {settings.resend_api_key.get_secret_value().strip()}"}
    imported = 0
    with factory() as session:
        client_id = session.scalar(
            select(Client.id).where(
                Client.slug == settings.email_intake_client_slug.strip(),
                Client.status == "active",
            )
        )
    if client_id is None:
        raise RuntimeError("email_intake_client_unavailable")

    for attachment in message.get("attachments") or []:
        name = attachment.get("filename") or ""
        filename = PurePosixPath(name.replace("\\", "/")).name
        if not filename.lower().endswith(".pdf"):
            continue
        message_id = quote(message["id"], safe="")
        attachment_id = quote(attachment["id"], safe="")
        attachment_key = hashlib.sha256(f"resend/{message_id}/{attachment_id}".encode()).hexdigest()
        key = attachment_key[: settings.max_idempotency_key_length]
        with factory() as session:
            if get_by_idempotency_key(session, client_id, key) is not None:
                continue
        details = http.get(
            f"{_RECEIVING_URL}/{message_id}/attachments/{attachment_id}",
            headers=headers,
        )
        details.raise_for_status()
        url = details.json()["download_url"]
        if urlsplit(url).scheme != "https":
            logger.warning("Resend returned a non-HTTPS download URL")
            continue
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            document = _download(http, url, directory, filename, settings.max_file_bytes)
            if document is None:
                logger.warning("Skipped email attachment with invalid type or size")
                continue
            raw = RawSubmission(
                # A resend is a new submission even when the PDF bytes match.
                # Polling and webhook deliveries share the same attachment identity.
                job_ref=f"email-{attachment_key}"[: settings.max_job_ref_length],
                webhook_url=None,
                documents=[document],
                temp_dir=directory,
            )
            try:
                validated = validate_submission(raw, settings)
                with factory() as session:
                    client = session.get(Client, client_id)
                    if client is None or client.status != "active":
                        raise RuntimeError("email_intake_client_unavailable")
                    result = accept(
                        session,
                        client=client,
                        validated=validated,
                        idempotency_key=key,
                        storage=storage,
                        settings=settings,
                        clock=clock,
                    )
            except DomainError as exc:
                if exc.http_status >= 500:
                    raise
                logger.warning("Email attachment rejected (%s)", exc.code)
                continue
            imported += 1
            logger.info("Email attachment accepted for job %s", result.job_id)
    return imported


def _download(
    http: httpx.Client, url: str, directory: Path, filename: str, max_bytes: int
) -> SpooledDocument | None:
    path = directory / "attachment.pdf"
    digest = hashlib.sha256()
    size = 0
    with http.stream("GET", url) as response, path.open("wb") as output:
        response.raise_for_status()
        for chunk in response.iter_bytes(chunk_size=65536):
            size += len(chunk)
            if size > max_bytes:
                return None
            output.write(chunk)
            digest.update(chunk)
    with path.open("rb") as source:
        if detect_media_type(source.read(1024)) != "pdf":
            return None
    return SpooledDocument(filename[:255], "application/pdf", path, size, digest.hexdigest(), "pdf")


async def poll_inbox(
    settings: Settings,
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    clock: Clock,
    stop: asyncio.Event,
) -> None:
    with httpx.Client(timeout=settings.resend_timeout_seconds) as http:
        while not stop.is_set():
            try:
                await asyncio.to_thread(import_inbox, settings, factory, storage, clock, http)
            except httpx.HTTPStatusError as exc:
                logger.warning("Email intake HTTP request failed (%s)", exc.response.status_code)
            except Exception as exc:
                logger.warning("Email intake failed (%s)", type(exc).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.email_intake_poll_seconds)
