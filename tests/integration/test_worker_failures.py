from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.integration.conftest import ApiHarness
from tests.support import make_pdf
from trade_extract.db.models.task import JobTask
from trade_extract.db.models.webhook import WebhookDelivery
from trade_extract.domain.clock import FrozenClock
from trade_extract.services import queue as queue_service
from trade_extract.services import webhooks as webhook_service
from trade_extract.services.queue import claim_task
from trade_extract.workers import maintenance
from trade_extract.workers.runner import run_once

pytestmark = pytest.mark.integration


def post_files(api: ApiHarness, data: dict[str, str], files: list[tuple]):
    return api.client.post("/v1/extract", headers=api.headers, data=data, files=files)


def docs(*payloads: bytes) -> list[tuple]:
    return [
        ("documents", (f"document-{index}.pdf", payload, "application/pdf"))
        for index, payload in enumerate(payloads)
    ]


def test_dead_task_terminalises_file_submission_and_job(api: ApiHarness, capsys) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    accepted = post_files(
        api,
        {"job_ref": "BL-FAIL", "webhook_url": "https://hooks.example.test/fail"},
        docs(make_pdf([["INVOICE"]])),
    )
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]

    def boom(session, task, *, settings, clock):
        raise RuntimeError("prepare exploded")

    for _ in range(3):
        assert run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="failing-worker",
            handlers={"prepare_file": boom},
        )
        clock.advance(seconds=60)

    with api.session_factory() as session:
        task = session.query(JobTask).filter(JobTask.kind == "prepare_file").one()
        assert task.status == "dead"
        assert task.attempts == 3
        file_row = session.execute(text("SELECT status FROM files")).scalar_one()
        assert file_row == "failed"
        submission = (
            session.execute(text("SELECT status, error_code, error_message FROM submissions"))
            .mappings()
            .one()
        )
        assert submission["status"] == "failed"
        assert submission["error_code"]
        assert "prepare exploded" in submission["error_message"]
        job = session.execute(text("SELECT status, terminal_at FROM jobs")).mappings().one()
        assert job["status"] == "failed"
        assert job["terminal_at"] is not None
        event = session.query(WebhookDelivery).one()
        assert event.event_type == "result.failed"
        assert event.status == "pending"

    polled = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "failed"
    assert body["decision"]["value"] == "failed"
    assert body["decision"]["reasons"]
    assert body["progress"]["documents_failed"] == 1
    printed = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"event": "pipeline.result"')
    ]
    assert len(printed) == 1
    assert printed[0]["result"]["job_id"] == accepted.json()["job_id"]
    assert printed[0]["result"]["status"] == "failed"
    assert "Retry-After" not in polled.headers


def test_stale_running_task_is_requeued_without_spending_a_retry(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    accepted = post_files(api, {"job_ref": "BL-STALE"}, docs(make_pdf()))
    assert accepted.status_code == 202

    with api.session_factory() as session:
        claimed = claim_task(session, "dead-worker", ["prepare_file"], clock.now())
        assert claimed is not None
        session.commit()
        claimed_id = claimed.id
        claimed.locked_at = clock.now() - timedelta(hours=1)
        session.commit()
        assert claimed.attempts == 1

    requeued = maintenance.requeue_stale_tasks_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=clock,
    )
    assert requeued == 1

    with api.session_factory() as session:
        task = session.get(JobTask, claimed_id)
        assert task is not None
        assert task.status == "queued"
        assert task.attempts == 0
        assert task.locked_by is None


def test_stale_delivering_webhook_is_reclaimed(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    accepted = post_files(
        api,
        {"job_ref": "BL-RECLAIM", "webhook_url": "https://hooks.example.test/hook"},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202

    with api.session_factory() as session:
        for _ in range(10):
            if (
                run_once(
                    settings=api.settings,
                    session_factory_=api.session_factory,
                    clock=clock,
                    worker_id="test-worker",
                )
                is False
            ):
                break
        delivery = webhook_service.claim_due_delivery(session, clock.now(), lease_seconds=60)
        assert delivery is not None
        session.commit()
        assert delivery.status == "delivering"

    reclaimed = maintenance.reclaim_stale_deliveries_once(
        session_factory_=api.session_factory,
        clock=clock,
    )
    assert reclaimed == 0  # the lease has not expired yet

    clock.advance(seconds=61)
    reclaimed = maintenance.reclaim_stale_deliveries_once(
        session_factory_=api.session_factory,
        clock=clock,
    )
    assert reclaimed == 1
    with api.session_factory() as session:
        assert session.query(WebhookDelivery).one().status == "pending"


def test_queue_requeue_keeps_attempt_budget(api: ApiHarness) -> None:
    accepted = post_files(api, {"job_ref": "BL-BUDGET"}, docs(make_pdf()))
    assert accepted.status_code == 202
    now = datetime.now(UTC)
    with api.session_factory() as session:
        job_id = session.execute(text("SELECT id FROM jobs")).scalar_one()
        task = JobTask(
            id="task_manual",
            job_id=job_id,
            kind="prepare_file",
            status="queued",
            attempts=0,
            max_attempts=3,
            locked_by=None,
            locked_at=None,
            payload={},
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        session.commit()
        task.status = "running"
        task.locked_by = "worker"
        task.locked_at = now - timedelta(hours=2)
        task.attempts = 2
        session.commit()

        outcome = queue_service.requeue_stale(
            session,
            older_than=now - timedelta(minutes=5),
            now=now,
        )
        session.commit()
        assert outcome.requeued == 1
        assert outcome.dead == ()
        assert task.status == "queued"
        assert task.attempts == 1
        assert task.requeues == 1


def test_dead_finalize_task_terminalises_the_submission(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    accepted = post_files(
        api,
        {"job_ref": "BL-FINALIZE-FAIL", "webhook_url": "https://hooks.example.test/fail"},
        docs(make_pdf([["INVOICE"]])),
    )
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=clock,
        worker_id="prep-worker",
        kinds=["prepare_file"],
    )

    def boom(session, task, *, settings, clock):
        raise RuntimeError("finalize exploded")

    for _ in range(3):
        assert run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="finalize-worker",
            kinds=["finalize_submission"],
            handlers={"finalize_submission": boom},
        )
        clock.advance(seconds=60)

    with api.session_factory() as session:
        task = session.query(JobTask).filter(JobTask.kind == "finalize_submission").one()
        assert task.status == "dead"
        submission = (
            session.execute(text("SELECT status, error_message FROM submissions")).mappings().one()
        )
        assert submission["status"] == "failed"
        assert "finalize exploded" in submission["error_message"]
        job = session.execute(text("SELECT status, terminal_at FROM jobs")).mappings().one()
        assert job["status"] == "failed"
        assert job["terminal_at"] is not None
        event = session.query(WebhookDelivery).one()
        assert event.event_type == "result.failed"

    polled = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert polled.status_code == 200
    assert polled.json()["status"] == "failed"
    assert polled.json()["decision"]["reasons"]


def test_requeue_ceiling_kills_a_finalize_task_without_rearming_it(api: ApiHarness) -> None:
    from trade_extract.db.repositories import tasks as tasks_repo

    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    accepted = post_files(
        api,
        {"job_ref": "BL-CEILING", "webhook_url": "https://hooks.example.test/fail"},
        docs(make_pdf([["ONE"]]), make_pdf([["TWO"]])),
    )
    assert accepted.status_code == 202

    with api.session_factory() as session:
        submission_id = session.execute(text("SELECT id FROM submissions")).scalar_one()
        job_id = session.execute(text("SELECT id FROM jobs")).scalar_one()
        task = tasks_repo.enqueue(
            session,
            job_id=job_id,
            submission_id=submission_id,
            kind="finalize_submission",
            payload={"submission_id": submission_id, "job_id": job_id},
            dedupe_key=f"finalize_submission:{submission_id}",
            priority=1000,
            now=clock.now(),
        )
        task.status = "running"
        task.locked_by = "crashed-worker"
        task.locked_at = clock.now() - timedelta(hours=2)
        task.attempts = 1
        task.requeues = api.settings.task_max_requeues
        session.commit()
        finalize_id = task.id

    assert (
        maintenance.requeue_stale_tasks_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
        )
        == 0
    )

    with api.session_factory() as session:
        task = session.get(JobTask, finalize_id)
        assert task is not None
        assert task.status == "dead"
        assert task.requeues == api.settings.task_max_requeues + 1
        assert task.last_error_code == "stale_requeue_limit"
        assert session.execute(text("SELECT status FROM submissions")).scalar_one() == "failed"
        assert session.execute(text("SELECT status FROM jobs")).scalar_one() == "failed"
        assert session.query(WebhookDelivery).filter_by(event_type="result.failed").count() == 1

    # A second sweep must not double-count or re-notify the already-dead task.
    assert (
        maintenance.requeue_stale_tasks_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
        )
        == 0
    )
    with api.session_factory() as session:
        assert session.get(JobTask, finalize_id).requeues == api.settings.task_max_requeues + 1
        assert session.query(WebhookDelivery).filter_by(event_type="result.failed").count() == 1

    # Remaining prepare tasks must not resurrect the dead finalize task.
    for _ in range(10):
        if not run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="late-prep-worker",
            kinds=["prepare_file"],
        ):
            break
    with api.session_factory() as session:
        assert session.get(JobTask, finalize_id).status == "dead"
        assert session.query(JobTask).filter(JobTask.kind == "finalize_submission").count() == 1
        assert session.query(WebhookDelivery).filter_by(event_type="result.failed").count() == 1


def test_maintenance_loop_survives_a_transient_error(api: ApiHarness, monkeypatch) -> None:
    calls = {"count": 0}

    def flaky(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("database hiccup")
        raise KeyboardInterrupt

    monkeypatch.setattr(maintenance, "run_webhook_once", flaky)
    monkeypatch.setattr(maintenance.time, "sleep", lambda *_: None)

    maintenance.run_forever(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=FrozenClock(datetime.now(UTC)),
    )

    assert calls["count"] == 2


def test_worker_loop_survives_a_claim_error(api: ApiHarness, monkeypatch) -> None:
    from trade_extract.workers import runner

    calls = {"count": 0}

    def flaky(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("claim failed")
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_once", flaky)
    monkeypatch.setattr(runner.time, "sleep", lambda *_: None)

    runner.run_forever(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=FrozenClock(datetime.now(UTC)),
        worker_id="loop-worker",
    )

    assert calls["count"] == 2


def _claim_and_expire_delivery(api: ApiHarness, clock: FrozenClock, job_ref: str) -> str:
    accepted = post_files(
        api,
        {"job_ref": job_ref, "webhook_url": "https://hooks.example.test/hook"},
        docs(make_pdf()),
    )
    assert accepted.status_code == 202
    with api.session_factory() as session:
        for _ in range(10):
            if not run_once(
                settings=api.settings,
                session_factory_=api.session_factory,
                clock=clock,
                worker_id="reclaim-worker",
            ):
                break
        delivery = webhook_service.claim_due_delivery(session, clock.now(), lease_seconds=60)
        assert delivery is not None
        session.commit()
        delivery_id = delivery.id
    clock.advance(seconds=61)
    return delivery_id


def test_reclaiming_a_stale_delivery_spends_an_attempt(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    api.settings.webhook_max_attempts = 5
    delivery_id = _claim_and_expire_delivery(api, clock, "BL-RECLAIM-ATTEMPT")

    reclaimed = maintenance.reclaim_stale_deliveries_once(
        session_factory_=api.session_factory,
        clock=clock,
        settings=api.settings,
    )
    assert reclaimed == 1
    with api.session_factory() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == "pending"
        assert delivery.attempts == 1


def test_repeated_reclaims_dead_letter_at_the_maximum(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    api.settings.webhook_max_attempts = 1
    delivery_id = _claim_and_expire_delivery(api, clock, "BL-RECLAIM-DEAD")

    reclaimed = maintenance.reclaim_stale_deliveries_once(
        session_factory_=api.session_factory,
        clock=clock,
        settings=api.settings,
    )
    assert reclaimed == 0
    with api.session_factory() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == "dead"
        assert delivery.attempts == 1
