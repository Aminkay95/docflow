from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.integration.test_lifecycle import docs, post_files
from tests.integration.test_review_trust import invoices, login
from tests.support import make_pdf
from tests.unit.test_extraction import fixture as extraction_fixture
from trade_extract.db.models.job import Job
from trade_extract.db.models.task import JobTask
from trade_extract.domain.clock import FrozenClock
from trade_extract.domain.inference import ProviderResult
from trade_extract.domain.provenance import ReaderKind
from trade_extract.providers.mock import MockProvider
from trade_extract.services.extraction import extract
from trade_extract.services.progress import write
from trade_extract.services.queue import claim_task
from trade_extract.workers.runner import run_once

pytestmark = pytest.mark.integration


def accepted(api):
    response = post_files(api, {"job_ref": "PROGRESS"}, docs(make_pdf([["INVOICE"]])))
    assert response.status_code == 202
    return response.json()["job_id"]


def test_progress_requires_session_and_queued_page_has_filename(api):
    job_id = accepted(api)
    url = f"/review/jobs/{job_id}/progress"
    assert api.client.get(url, follow_redirects=False).status_code in (303, 401)
    login(api, role="auditor")
    response = api.client.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["processing"] is True
    assert body["stages"] == []
    assert body["steps_completed"] == 0
    assert [(step["id"], step["status"]) for step in body["steps"]] == [
        ("prepare", "waiting"),
        ("classify", "pending"),
        ("extract", "pending"),
        ("validate", "pending"),
    ]
    assert body["files"][0]["name"] == "document-0.pdf"
    page = api.client.get(f"/review/jobs/{job_id}")
    assert page.status_code == 200
    assert "Preparing your documents" in page.text
    assert "data-job-progress" in page.text
    assert "Extracting details" in page.text
    assert 'max="4" value="0"' in page.text
    assert page.text.count("data-progress-step=") == 4
    assert "data-autorefresh" not in page.text


def test_progress_is_visible_from_another_session_before_handler_returns(api, tmp_path):
    job_id = accepted(api)
    login(api)

    source, page, payload = extraction_fixture(tmp_path)

    class BlockedProvider(MockProvider):
        def text_reason(self, request):
            # While the provider call is in flight, a separate authenticated session sees progress.
            body = api.client.get(f"/review/jobs/{job_id}/progress").json()
            assert body["processing"] is True
            assert body["title"] == "Extracting fields"
            assert body["steps_completed"] == 2
            assert [step["status"] for step in body["steps"]] == [
                "complete",
                "complete",
                "active",
                "pending",
            ]
            assert any(stage["status"] == "active" for stage in body["stages"])
            return super().text_reason(request)

    provider = BlockedProvider(
        {
            ("commercial_invoice", source.document_hash): [
                ProviderResult(payload(ReaderKind.TEXT_LAYER), "mock-text")
            ]
        }
    )

    def blocked_handler(session, task, *, settings, clock):
        result = extract(
            provider,
            document_type="commercial_invoice",
            source=source,
            pages=(page,),
            progress=session.info["extraction_progress"],
        )
        assert result.status == "extracted"
        assert provider.requests
        assert "file_id" in task.payload

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=FrozenClock(datetime.now(UTC)),
        worker_id="progress-worker",
        handlers={"prepare_file": blocked_handler},
    )


def test_old_owner_and_reclaimed_attempt_cannot_update_progress(api):
    accepted(api)
    clock = FrozenClock(datetime.now(UTC))
    with api.session_factory() as session:
        task = claim_task(session, "old", ["prepare_file"], clock.now())
        session.commit()
        task_id, attempt, claimed_at = task.id, task.attempts, task.locked_at
    assert write(api.session_factory, task_id, "old", attempt, claimed_at, clock, "reading")
    with api.session_factory() as session:
        task = session.get(JobTask, task_id)
        payload = task.payload
        task.locked_by = "new"
        task.locked_at = claimed_at + timedelta(seconds=1)
        session.commit()
    assert not write(api.session_factory, task_id, "old", attempt, claimed_at, clock, "extracting")
    with api.session_factory() as session:
        task = session.get(JobTask, task_id)
        assert task.payload == payload
        current_at = task.locked_at
    assert write(api.session_factory, task_id, "new", attempt, current_at, clock, "preparing")
    with api.session_factory() as session:
        metadata = session.get(JobTask, task_id).payload["progress"]
        assert [stage["stage"] for stage in metadata["stages"]] == ["preparing"]


def test_terminal_reviews_and_pending_finalization_are_distinct(api, tmp_path, monkeypatch):
    result = invoices(api, tmp_path, monkeypatch)
    headers = login(api)
    url = f"/review/jobs/{result['job_id']}/progress"
    body = api.client.get(url).json()
    assert body["state"] == "review"
    assert body["steps_completed"] == 4
    assert all(step["status"] == "complete" for step in body["steps"])
    assert body["fields"] > 0
    assert body["reviews"] > 0
    html = api.client.get(f"/review/jobs/{result['job_id']}").text
    progress_html = html.split('class="card extraction-progress"', 1)[1].split("</section>", 1)[0]
    assert progress_html.index("data-progress-counts") < progress_html.index("<details")
    assert "data-progress-review" in progress_html.split("</details>", 1)[1]
    assert '<button type="button" class="btn btn-secondary" data-progress-review' in progress_html
    assert "Observed" not in progress_html
    assert "data-progress-history open" not in progress_html
    assert 'id="job-review-workspace" tabindex="-1"' in html
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "finalize_submission"))
        task.status = "queued"
        session.commit()
    body = api.client.get(url).json()
    assert body["processing"] is True
    assert (
        api.client.post(f"/review/jobs/{result['job_id']}/done", headers=headers).status_code == 409
    )


def test_failed_processing_has_no_raw_diagnostics(api):
    job_id = accepted(api)
    login(api)
    with api.session_factory() as session:
        job = session.get(Job, job_id)
        job.status = "failed"
        from trade_extract.db.models.submission import Submission

        submission = session.scalar(select(Submission).where(Submission.job_id == job_id))
        submission.status = "failed"
        task = session.scalar(select(JobTask).where(JobTask.job_id == job_id))
        task.status = "dead"
        task.last_error = "secret supplier 123"
        session.commit()
    response = api.client.get(f"/review/jobs/{job_id}/progress")
    assert response.json()["state"] == "failed"
    assert "secret supplier" not in response.text


def test_pending_sibling_preserves_review_and_historical_jobs_have_no_invented_stages(
    api, tmp_path, monkeypatch
):
    from trade_extract.db.models.document import Document
    from trade_extract.db.models.evidence import ReviewItem
    from trade_extract.db.models.submission import Submission

    result = invoices(api, tmp_path, monkeypatch, currencies=("USD", "USD"))
    login(api)
    with api.session_factory() as session:
        rows = list(session.scalars(select(Document).order_by(Document.created_at, Document.id)))
        ready, pending = rows
        ready_id = ready.id
        retained = pending.extraction
        pending.status = "processing"
        pending.extraction = None
        item = session.scalar(select(ReviewItem).where(ReviewItem.document_id == pending.id))
        item.status = "resolved"
        submission = session.get(Submission, pending.submission_id)
        submission.status = "processing"
        session.commit()
    url = f"/review/jobs/{result['job_id']}"
    body = api.client.get(url + "/progress").json()
    assert body["processing"] is True
    assert body["documents"] == 2 and body["documents_ready"] == 1
    assert body["fields"] > 0
    assert any(stage["status"] == "partial" for stage in body["stages"])
    assert not any(stage["status"] == "complete" for stage in body["stages"])
    page = api.client.get(url + f"?document_id={ready_id}")
    assert page.status_code == 200
    assert "Completed in earlier work" in page.text
    assert f'data-document-id="{ready_id}"' in page.text
    assert "data-finish-job=" in page.text
    with api.session_factory() as session:
        pending = session.get(Document, rows[1].id)
        pending.status = "review_required"
        pending.extraction = retained
        session.get(Submission, pending.submission_id).status = "completed"
        for task in session.scalars(select(JobTask).where(JobTask.job_id == result["job_id"])):
            task.payload = {key: value for key, value in task.payload.items() if key != "progress"}
        actual_end = session.get(Submission, pending.submission_id).completed_at
        session.get(Job, result["job_id"]).updated_at += timedelta(days=1)
        session.commit()
    historical = api.client.get(url + "/progress").json()
    assert historical["processing"] is False and historical["state"] == "review"
    assert historical["stages"] == [] and historical["updated_at"] is None
    assert historical["terminal_at"] == actual_end.isoformat()
    assert "Detailed stage times are unavailable for this job." in api.client.get(url).text


def test_projection_ignores_requeued_and_reclaimed_stages_but_keeps_terminal_history(api):
    job_id = accepted(api)
    login(api)
    clock = FrozenClock(datetime.now(UTC))
    with api.session_factory() as session:
        task = claim_task(session, "old", ["prepare_file"], clock.now())
        session.commit()
        task_id, attempt, claimed_at = task.id, task.attempts, task.locked_at
    assert write(api.session_factory, task_id, "old", attempt, claimed_at, clock, "reading")
    url = f"/review/jobs/{job_id}/progress"
    with api.session_factory() as session:
        task = session.get(JobTask, task_id)
        task.status = "queued"
        task.locked_at = None
        task.locked_by = None
        session.commit()
    queued = api.client.get(url).json()
    assert queued["stages"] == []
    assert queued["updated_at"] is None

    with api.session_factory() as session:
        task = session.get(JobTask, task_id)
        task.status = "running"
        task.locked_at = claimed_at + timedelta(seconds=1)
        task.locked_by = "new"
        session.commit()
        new_claim = task.locked_at
    reclaimed = api.client.get(url).json()
    assert reclaimed["title"] == "Preparing source pages"
    assert [stage["id"] for stage in reclaimed["stages"]] == ["preparing"]
    assert reclaimed["stages"][0]["at"] is None
    assert reclaimed["updated_at"] is None

    assert write(api.session_factory, task_id, "new", attempt, new_claim, clock, "preparing")
    with api.session_factory() as session:
        task = session.get(JobTask, task_id)
        task.status = "succeeded"
        task.locked_at = None
        task.locked_by = None
        session.commit()
    terminal = api.client.get(url).json()
    assert terminal["stages"][0]["id"] == "preparing"
    assert terminal["stages"][0]["status"] == "partial"
    assert terminal["stages"][0]["at"] is not None


@pytest.mark.parametrize(
    ("kind", "stage", "title"),
    [
        ("prepare_file", "preparing", "Preparing source pages"),
        ("prepare_file", "identifying", "Identifying documents"),
        ("extract_segment", "extracting", "Extracting fields"),
    ],
)
def test_running_finalizer_does_not_hide_unfinished_pipeline_work(api, kind, stage, title):
    job_id = accepted(api)
    login(api)
    clock = FrozenClock(datetime.now(UTC))
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "prepare_file"))
        task.kind = kind
        task.status = "running"
        task.locked_by = "extractor"
        task.locked_at = clock.now()
        task.attempts = 1
        task_id, claimed_at = task.id, task.locked_at
        finalizer = session.scalar(select(JobTask).where(JobTask.kind == "finalize_submission"))
        if finalizer is None:
            finalizer = JobTask(
                id="tsk_early_finalize",
                job_id=job_id,
                submission_id=task.submission_id,
                kind="finalize_submission",
            )
            session.add(finalizer)
        finalizer.status = "running"
        finalizer.locked_by = "finalizer"
        finalizer.locked_at = clock.now()
        finalizer.attempts = 1
        finalizer.created_at = clock.now() - timedelta(days=1)
        session.commit()
        finalizer_id, finalizer_claim = finalizer.id, finalizer.locked_at
    assert write(
        api.session_factory, finalizer_id, "finalizer", 1, finalizer_claim, clock, "finalizing"
    )
    assert write(api.session_factory, task_id, "extractor", 1, claimed_at, clock, stage)
    body = api.client.get(f"/review/jobs/{job_id}/progress").json()
    assert body["processing"] is True
    assert body["title"] == title
    assert next(row for row in body["stages"] if row["id"] == stage)["status"] == "active"


def test_initial_progress_report_error_uses_existing_task_retry(api, monkeypatch):
    accepted(api)
    called = []

    def unavailable(*args, **kwargs):
        raise RuntimeError("progress database unavailable")

    monkeypatch.setattr("trade_extract.workers.runner.progress.write", unavailable)
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=FrozenClock(datetime.now(UTC)),
        worker_id="reporter-failure",
        handlers={"prepare_file": lambda *args, **kwargs: called.append(True)},
    )
    assert called == []
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "prepare_file"))
        assert task.status == "queued"
        assert task.attempts == 1
        assert task.locked_by is None
        assert task.last_error_code == "RuntimeError"


def test_pending_work_starts_do_not_change_workspace_version(api, tmp_path, monkeypatch):
    from trade_extract.db.models.document import Document
    from trade_extract.db.models.file import File
    from trade_extract.review.progress import project

    result = invoices(api, tmp_path, monkeypatch)
    with api.session_factory() as session:
        document = session.scalar(select(Document))
        document.status = "queued"
        document.extraction = None
        session.commit()
        job = session.get(Job, result["job_id"])
        before = project(session, job)
        document.status = "processing"
        document.updated_at += timedelta(minutes=1)
        session.scalar(select(File)).status = "processing"
        session.commit()
        during = project(session, job)
        assert during["version"] == before["version"]
        document.status = "review_required"
        document.extraction = {"fields": {"invoice.number": {"value": "retained"}}}
        session.commit()
        after = project(session, job)
        assert after["version"] != during["version"]
        assert after["fields"] == 1


def test_latest_upload_owns_timer_and_failed_task_can_freeze_it(api):
    from trade_extract.db.models.submission import Submission
    from trade_extract.review.progress import project

    job_id = accepted(api)
    clock = datetime.now(UTC)
    with api.session_factory() as session:
        job = session.get(Job, job_id)
        job.created_at = clock - timedelta(days=3)
        old = session.scalar(select(Submission))
        old.created_at = clock - timedelta(days=2)
        old.status = "completed"
        old.completed_at = clock - timedelta(days=2, seconds=-30)
        latest = Submission(
            id="sub_latest",
            result_id="res_latest",
            job_id=job_id,
            client_id=api.client_id,
            status="processing",
            created_at=clock,
        )
        session.add(latest)
        task = session.scalar(select(JobTask).where(JobTask.kind == "prepare_file"))
        task.submission_id = latest.id
        session.commit()
        assert project(session, job)["started_at"] == clock.isoformat()
        job.status = "failed"
        latest.status = "failed"
        task.status = "dead"
        task.finished_at = clock + timedelta(seconds=35)
        session.commit()
        failed = project(session, job)
        assert failed["state"] == "failed"
        assert failed["terminal_at"] == task.finished_at.isoformat()
        task.finished_at = None
        session.commit()
        assert project(session, job)["terminal_at"] is None
        job.status = "completed"
        task.status = "succeeded"
        latest.status = "completed"
        latest.completed_at = clock + timedelta(seconds=30)
        old.completed_at = clock + timedelta(seconds=90)
        session.commit()
        assert project(session, job)["terminal_at"] == old.completed_at.isoformat()


def test_field_counts_are_projected_without_loading_extraction_documents(
    api, tmp_path, monkeypatch
):
    from trade_extract.db.models.document import Document
    from trade_extract.review.progress import project

    result = invoices(api, tmp_path, monkeypatch, currencies=("USD", "USD"))
    with api.session_factory() as session:
        rows = list(session.scalars(select(Document)))
        rows[0].extraction = {"fields": {"one": {"value": "private"}, "two": {"value": "private"}}}
        rows[1].extraction = None
        session.commit()
    with api.session_factory() as session:
        body = project(session, session.get(Job, result["job_id"]))
        assert body["fields"] == 2
        assert not any(isinstance(row, Document) for row in session.identity_map.values())
        assert "private" not in str(body)


def test_production_extraction_handler_persists_provider_and_validation_stages(api, monkeypatch):
    from trade_extract.domain.inference import ProviderError

    result = post_files(
        api,
        {"job_ref": "REAL-HANDLER"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Invoice INV-001 Seller Buyer Goods USD Total 10"]])),
    ).json()
    job_id = result["job_id"]
    login(api)
    calls = []

    class ProbeProvider:
        name = "mock"

        def text_reason(self, request):
            calls.append(request)
            body = api.client.get(f"/review/jobs/{job_id}/progress").json()
            assert body["title"] == "Extracting fields"
            assert any(
                row["id"] == "extracting" and row["status"] == "active" for row in body["stages"]
            )
            raise ProviderError("injected_test_failure")

        vision_extract = text_reason

    monkeypatch.setattr(
        "trade_extract.workers.handlers.extract_segment.make_provider",
        lambda *args, **kwargs: ProbeProvider(),
    )
    settings = api.settings.model_copy(update={"llm_extraction_priority": True})
    clock = FrozenClock(datetime.now(UTC))
    for kind in ("prepare_file", "extract_segment"):
        assert run_once(
            settings=settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="production-handler-test",
            kinds=[kind],
        )
    assert calls
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "extract_segment"))
        assert task.status == "succeeded"
        stages = [row["stage"] for row in task.payload["progress"]["stages"]]
        assert stages.index("extracting") < stages.index("validating")
