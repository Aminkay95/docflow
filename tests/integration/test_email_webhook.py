import base64
import hmac
import json

import httpx
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from tests.support import make_pdf
from trade_extract.db.models.client import Client
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.workers import runner
from trade_extract.workers.handlers import import_email


def test_resend_signature_and_duplicate_delivery(api, monkeypatch):
    key = b"test webhook signing key"
    api.settings.email_intake_webhook_secret = SecretStr("whsec_" + base64.b64encode(key).decode())
    api.settings.email_intake_address = "intake@example.com"
    with api.session_factory() as session:
        api.settings.email_intake_client_slug = session.get(Client, api.client_id).slug
    timestamp = str(int(api.app.state.clock.now().timestamp()))
    body = json.dumps(
        {
            "type": "email.received",
            "data": {
                "email_id": "email-1",
                "to": ["intake@example.com"],
                "attachments": [{"id": "pdf-1", "filename": "invoice.pdf"}],
            },
        }
    ).encode()
    endpoint = "/v1/webhooks/resend/inbound"
    assert api.client.post(endpoint, content=body).status_code == 401
    for event_id in ("msg_1", "msg_1", "msg_2"):
        signature = base64.b64encode(
            hmac.digest(key, f"{event_id}.{timestamp}.".encode() + body, "sha256")
        ).decode()
        response = api.client.post(
            endpoint,
            content=body,
            headers={
                "svix-id": event_id,
                "svix-timestamp": timestamp,
                "svix-signature": f"v1,{signature}",
            },
        )
        assert response.status_code == 200
    with api.session_factory() as session:
        task = session.scalars(select(JobTask)).one()
        assert task.kind == "import_email"
        assert task.job_id is None
        assert task.status == "queued"
        assert task.payload["attachments"] == [{"id": "pdf-1", "filename": "invoice.pdf"}]

    def resend(request):
        if request.url.host == "api.resend.com":
            return httpx.Response(200, json={"download_url": "https://files.example.com/invoice"})
        assert request.url.host == "files.example.com"
        return httpx.Response(200, content=make_pdf())

    real_client = httpx.Client
    monkeypatch.setattr(
        import_email.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(resend), **kwargs),
    )
    try:
        assert runner.run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=api.app.state.clock,
            worker_id="email-test-worker",
            kinds=["import_email"],
        )
        with api.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1
            task = session.scalar(select(JobTask).where(JobTask.kind == "import_email"))
            assert task.status == "succeeded"
    finally:
        with api.session_factory.begin() as session:
            session.execute(delete(JobTask).where(JobTask.kind == "import_email"))
