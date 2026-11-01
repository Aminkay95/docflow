from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.integration.conftest import ApiHarness, count_rows, seed_client
from tests.support import make_pdf
from trade_extract.domain.clock import FrozenClock
from trade_extract.services.webhooks import signature_for
from trade_extract.workers import maintenance
from trade_extract.workers.runner import run_once

WEBHOOK_SECRET = "whsec_test_secret"

pytestmark = pytest.mark.integration


@dataclass
class CapturedRequest:
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class WebhookServer:
    server: ThreadingHTTPServer
    requests: list[CapturedRequest] = field(default_factory=list)
    response_code: int = 204

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/hook"


@pytest.fixture
def webhook_server():
    holder: dict[str, WebhookServer] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            harness = holder["server"]
            harness.requests.append(
                CapturedRequest(
                    path=self.path,
                    headers={key: value for key, value in self.headers.items()},
                    body=body,
                )
            )
            self.send_response(harness.response_code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    harness = WebhookServer(server=server)
    holder["server"] = harness
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield harness
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post_files(api: ApiHarness, data: dict[str, str], files: list[tuple]):
    return api.client.post("/v1/extract", headers=api.headers, data=data, files=files)


def docs(*payloads: bytes) -> list[tuple]:
    return [
        ("documents", (f"document-{index}.pdf", payload, "application/pdf"))
        for index, payload in enumerate(payloads)
    ]


def complete_submission(api: ApiHarness) -> None:
    for _ in range(20):
        if not run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=api.app.state.clock,
            worker_id="test-worker",
        ):
            return
    raise AssertionError("worker did not drain")


def test_completion_delivers_signed_event(api: ApiHarness, webhook_server: WebhookServer) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-HOOK", "webhook_url": webhook_server.url},
        docs(make_pdf([["INVOICE"]])),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]

    complete_submission(api)
    assert count_rows(api, "webhook_deliveries") == 1

    assert maintenance.run_webhook_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=clock,
    )
    assert len(webhook_server.requests) == 1
    request = webhook_server.requests[0]
    assert request.path == "/hook"
    assert request.headers["X-TradeExtract-Event"] == "result.completed"
    assert request.headers["X-TradeExtract-Delivery"].startswith("whd_")
    timestamp = int(request.headers["X-TradeExtract-Timestamp"])
    assert request.headers["X-TradeExtract-Signature"] == signature_for(
        WEBHOOK_SECRET, timestamp, request.body
    )

    payload = json.loads(request.body)
    assert payload["type"] == "result.completed"
    assert payload["data"]["result_id"] == result_id
    assert payload["data"]["result_url"].endswith(f"/v1/results/{result_id}")
    assert "documents" not in payload["data"]
    assert "fields" not in payload["data"]

    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "delivered"
        assert delivery.attempts == 1
        assert delivery.delivered_at is not None


def test_client_default_webhook_is_used_when_submission_omits_one(
    api: ApiHarness, webhook_server: WebhookServer
) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    token, _, _ = seed_client(api.session_factory, api.settings, webhook_url=webhook_server.url)
    headers = {"Authorization": f"Bearer {token}"}

    accepted = api.client.post(
        "/v1/extract",
        headers=headers,
        data={"job_ref": "BL-DEFAULT"},
        files=docs(make_pdf()),
    )
    assert accepted.status_code == 202, accepted.text
    complete_submission(api)
    assert maintenance.run_webhook_once(
        settings=api.settings, session_factory_=api.session_factory, clock=clock
    )
    assert len(webhook_server.requests) == 1
    assert json.loads(webhook_server.requests[0].body)["data"]["job_ref"] == "BL-DEFAULT"


def test_webhook_500_retries_with_backoff(api: ApiHarness, webhook_server: WebhookServer) -> None:
    webhook_server.response_code = 500
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-RETRY", "webhook_url": webhook_server.url},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    complete_submission(api)

    assert maintenance.run_webhook_once(
        settings=api.settings, session_factory_=api.session_factory, clock=clock
    )
    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "failed"
        assert delivery.attempts == 1
        assert delivery.next_attempt_at > clock.now()

    clock.advance(minutes=1, seconds=1)
    assert maintenance.run_webhook_once(
        settings=api.settings, session_factory_=api.session_factory, clock=clock
    )
    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.attempts == 2


def test_webhook_410_marks_dead(api: ApiHarness, webhook_server: WebhookServer) -> None:
    webhook_server.response_code = 410
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-GONE", "webhook_url": webhook_server.url},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    complete_submission(api)
    maintenance.run_webhook_once(
        settings=api.settings, session_factory_=api.session_factory, clock=clock
    )
    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "dead"
        assert delivery.last_status_code == 410


def test_webhook_other_4xx_dies_after_min_of_three_attempts(
    api: ApiHarness, webhook_server: WebhookServer
) -> None:
    webhook_server.response_code = 404
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-404", "webhook_url": webhook_server.url},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    complete_submission(api)

    for attempt in range(3):
        if attempt:
            clock.advance(seconds=301)
        assert maintenance.run_webhook_once(
            settings=api.settings, session_factory_=api.session_factory, clock=clock
        )

    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "dead"
        assert delivery.attempts == 3


def test_webhook_5xx_dies_at_the_configured_maximum(
    api: ApiHarness, webhook_server: WebhookServer
) -> None:
    webhook_server.response_code = 503
    api.settings.webhook_max_attempts = 2
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-503", "webhook_url": webhook_server.url},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    complete_submission(api)

    for attempt in range(2):
        if attempt:
            clock.advance(seconds=61)
        assert maintenance.run_webhook_once(
            settings=api.settings, session_factory_=api.session_factory, clock=clock
        )

    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "dead"
        assert delivery.attempts == 2


def test_transient_dns_failure_leaves_the_delivery_retryable(api: ApiHarness, monkeypatch) -> None:
    import socket

    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    api.settings.webhook_allow_private_targets = False
    accepted = post_files(
        api,
        {"job_ref": "BL-DNS", "webhook_url": "https://hooks.example.test/hook"},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    complete_submission(api)

    def temporary_failure(*args, **kwargs):
        raise socket.gaierror(socket.EAI_AGAIN, "temporary failure in name resolution")

    monkeypatch.setattr("trade_extract.services.webhooks.socket.getaddrinfo", temporary_failure)
    assert maintenance.run_webhook_once(
        settings=api.settings, session_factory_=api.session_factory, clock=clock
    )
    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "failed"
        assert delivery.attempts == 1
        assert delivery.next_attempt_at > clock.now()


def test_transport_failure_is_retried_not_dead(
    api: ApiHarness, webhook_server: WebhookServer
) -> None:
    import httpx

    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-TRANSPORT", "webhook_url": webhook_server.url},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    complete_submission(api)

    class RefusingClient:
        def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    assert maintenance.run_webhook_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=clock,
        http_client=RefusingClient(),
    )

    with api.session_factory() as session:
        from trade_extract.db.models.webhook import WebhookDelivery

        delivery = session.query(WebhookDelivery).one()
        assert delivery.status == "failed"
        assert delivery.attempts == 1
        assert delivery.next_attempt_at > clock.now()
        assert delivery.last_error == "ConnectError"
        assert delivery.delivered_at is None


def test_disabled_client_webhook_is_not_used(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    token, _, _ = seed_client(
        api.session_factory,
        api.settings,
        webhook_url="https://hooks.example.test/hook",
        webhook_enabled=False,
    )

    accepted = api.client.post(
        "/v1/extract",
        headers={"Authorization": f"Bearer {token}"},
        data={"job_ref": "BL-DISABLED"},
        files=docs(make_pdf()),
    )
    assert accepted.status_code == 202, accepted.text
    complete_submission(api)

    assert count_rows(api, "webhook_deliveries") == 0
