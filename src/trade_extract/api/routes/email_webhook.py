"""Authenticate Resend notifications and persist inbound PDF work."""

import base64
import hashlib
import hmac
import json
from email.utils import parseaddr

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from trade_extract.db.models.task import JobTask
from trade_extract.domain.ids import new_id

router = APIRouter()
_MAX_EVENT_BYTES = 128 * 1024


class Attachment(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    filename: str | None = None


class ReceivedEmail(BaseModel):
    email_id: str = Field(min_length=1, max_length=128)
    to: list[str]
    attachments: list[Attachment] = Field(default_factory=list)


def _valid_signature(request: Request, body: bytes, secret: str, now: float) -> bool:
    event_id = request.headers.get("svix-id", "")
    timestamp = request.headers.get("svix-timestamp", "")
    signatures = request.headers.get("svix-signature", "")
    if not event_id or not secret.startswith("whsec_"):
        return False
    try:
        if abs(now - int(timestamp)) > 300:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except ValueError:
        return False
    if not key:
        return False
    message = f"{event_id}.{timestamp}.".encode() + body
    expected = b"v1," + base64.b64encode(hmac.digest(key, message, "sha256"))
    return any(hmac.compare_digest(expected, item.encode()) for item in signatures.split())


def _enqueue(factory, payload: dict) -> None:
    dedupe = hashlib.sha256(payload["id"].encode()).hexdigest()
    with factory.begin() as session:
        session.execute(
            insert(JobTask)
            .values(
                id=new_id("task_"),
                kind="import_email",
                payload=payload,
                dedupe_key=f"resend:email:{dedupe}",
            )
            .on_conflict_do_nothing(index_elements=[JobTask.dedupe_key])
        )


@router.post("/webhooks/resend/inbound", include_in_schema=False)
async def receive(request: Request) -> Response:
    state = request.app.state
    settings = state.settings
    secret = settings.email_intake_webhook_secret.get_secret_value().strip()
    recipient = settings.email_intake_address.strip().casefold()
    if not (secret and recipient):
        raise HTTPException(404)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_EVENT_BYTES:
            raise HTTPException(413)
        body.extend(chunk)
    if not _valid_signature(request, bytes(body), secret, state.clock.now().timestamp()):
        raise HTTPException(401, "Invalid Resend signature")
    try:
        event = json.loads(body)
        if not isinstance(event, dict):
            raise ValueError
        if event.get("type") != "email.received":
            return Response(status_code=200)
        email = ReceivedEmail.model_validate(event.get("data"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, "Invalid Resend event") from exc
    if [parseaddr(value)[1].casefold() for value in email.to] != [recipient]:
        return Response(status_code=200)
    attachments = [
        item.model_dump()
        for item in email.attachments
        if (item.filename or "").lower().endswith(".pdf")
    ]
    if attachments:
        await run_in_threadpool(
            _enqueue,
            state.session_factory,
            {"id": email.email_id, "to": email.to, "attachments": attachments},
        )
    return Response(status_code=200)
