from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from tests.integration.conftest import count_rows, seed_client
from tests.integration.test_lifecycle import docs, drain, post_files
from tests.support import make_pdf
from tests.unit.test_invoice_gate_conflicts import invoice
from trade_extract.db.models.document import Document
from trade_extract.db.models.policy import Policy
from trade_extract.db.models.task import JobTask
from trade_extract.db.models.webhook import WebhookDelivery
from trade_extract.domain.clock import FrozenClock
from trade_extract.domain.inference import ExtractionOutcome
from trade_extract.schemas.invoice import CommercialInvoice
from trade_extract.workers.handlers import extract_segment
from trade_extract.workers.runner import run_once

pytestmark = pytest.mark.integration


def scripted_extraction(tmp_path, monkeypatch, api, *, currencies=("USD",)):
    raw = invoice(tmp_path).model_dump(mode="json")
    position = 0

    def extract(provider, *, source, pages, segment_id, **kwargs):
        nonlocal position
        # Inference observes no open transaction on the handler's connection.
        with api.session_factory() as probe:
            assert (
                probe.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction' "
                        "AND datname = current_database()"
                    )
                )
                == 0
            )
        assert pages and pages[0].transform is not None
        assert pages[0].characters
        value = deepcopy(raw)
        value["source"] = source.model_dump(mode="json")
        value["segment_id"] = segment_id
        value["fields"]["invoice.currency"]["value"] = currencies[
            min(position, len(currencies) - 1)
        ]
        position += 1
        for field in value["fields"].values():
            field["provenance"]["document_hash"] = source.document_hash
            field["provenance"]["page_index"] = source.page_start
        return ExtractionOutcome("extracted", document=CommercialInvoice.model_validate(value))

    monkeypatch.setattr(extract_segment, "extract", extract)


def test_invoice_completion_snapshot_retry_and_policy_history(api, tmp_path, monkeypatch):
    scripted_extraction(tmp_path, monkeypatch, api)
    accepted = post_files(
        api,
        {"job_ref": "INVOICE-REAL-LIFECYCLE", "webhook_url": "http://127.0.0.1/hook"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods", "Invoice 123 USD Total 10"]])),
    )
    assert accepted.status_code == 202
    result_id = accepted.json()["result_id"]
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="prepare",
        kinds=["prepare_file"],
    )
    midflight = api.client.get(f"/v1/results/{result_id}", headers=api.headers).json()
    assert midflight["progress"]["documents_completed"] == 0
    assert midflight["status"] == "processing"
    drain(api)
    body = api.client.get(f"/v1/results/{result_id}", headers=api.headers).json()
    assert body["status"] == "completed"
    document = body["documents"][0]
    assert document["fields"]["totals.payable_amount"]["value"] == "10"
    assert document["decision"]["value"] == "review"
    assert document["decision"]["policy_snapshot"]["review_depth"] == "full"
    assert body["review"] == {"extraction_open": 1, "conflict_open": 0}
    with api.session_factory() as session:
        delivery = session.scalar(select(WebhookDelivery))
        assert delivery.payload["data"]["result"] == body
        check_count = count_rows(api, "checks")
        decision_count = count_rows(api, "decisions")
        task = session.scalar(select(JobTask).where(JobTask.kind == "extract_segment"))
        task.status = "queued"
        task.run_after = datetime.now(UTC)
        policy = session.scalar(select(Policy))
        policy.config = {"invoice_pass_enabled": True, "review_depth": "checks"}
        session.commit()
    drain(api)
    assert count_rows(api, "checks") == check_count
    assert count_rows(api, "decisions") == decision_count
    after = api.client.get(f"/v1/results/{result_id}", headers=api.headers).json()
    assert after["documents"][0]["decision"] == document["decision"]


def test_one_conflict_has_three_sources_and_is_tenant_scoped(api, tmp_path, monkeypatch):
    scripted_extraction(tmp_path, monkeypatch, api, currencies=("USD", "EUR", "USD"))
    accepted = post_files(
        api,
        {"job_ref": "CONFLICT-JOB"},
        docs(
            *[
                make_pdf(
                    [["COMMERCIAL INVOICE", f"Seller Buyer Goods {i}", "Invoice USD Total 10"]]
                )
                for i in range(3)
            ]
        ),
    )
    drain(api)
    result_id = accepted.json()["result_id"]
    body = api.client.get(f"/v1/results/{result_id}", headers=api.headers).json()
    assert len(body["conflicts"]) == 1
    conflict = body["conflicts"][0]
    assert len(conflict["sources"]) == 3
    assert {s["stated_value"] for s in conflict["sources"]} == {"USD", "EUR"}
    assert body["decision"]["route"] == "conflict"
    assert body["review"] == {"extraction_open": 3, "conflict_open": 1}
    assert count_rows(api, "review_items") == 4
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "finalize_submission"))
        task.status = "queued"
        task.run_after = datetime.now(UTC)
        session.commit()
    drain(api)
    assert count_rows(api, "conflicts") == 1 and count_rows(api, "conflict_sources") == 3
    token, _, _ = seed_client(api.session_factory, api.settings)
    other = api.client.get(f"/v1/results/{result_id}", headers={"Authorization": f"Bearer {token}"})
    assert other.status_code == 404


def test_exhausted_extraction_failure_terminalizes_document(api, tmp_path, monkeypatch):
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock

    def unavailable(*args, **kwargs):
        raise RuntimeError("offline_provider")

    monkeypatch.setattr(extract_segment, "extract", unavailable)
    accepted = post_files(
        api,
        {"job_ref": "DEAD-INVOICE"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Goods Invoice Total USD 10"]])),
    )
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=clock,
        worker_id="prepare",
        kinds=["prepare_file"],
    )
    for _ in range(3):
        assert run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="extract",
            kinds=["extract_segment"],
        )
        clock.advance(seconds=600)
    drain(api)
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert body["status"] == "failed"
    assert body["documents"][0]["status"] == "failed"
    assert body["documents"][0]["decision"]["value"] == "failed"
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "extract_segment"))
        assert task.status == "dead" and task.attempts == 3


def test_worker_uses_reusable_extractor_with_injected_provider(api, monkeypatch):
    import json

    from trade_extract.domain.inference import ProviderResult
    from trade_extract.domain.provenance import BBox
    from trade_extract.schemas.invoice import INVOICE_FIELD_SET

    values = ["INV-ACTUAL", "2026-11-03", "USD", "Seller", "Buyer", "10", "10", "10", "10"]
    specs = [s for s in INVOICE_FIELD_SET.specs if s.required and not s.indexed]
    calls = []

    class ObservedProvider:
        name = "mock"
        payload = None

        def text_reason(self, request):
            calls.append("text")
            identity = json.loads(request.evidence.split("\n")[0])
            geometry = json.loads(request.evidence.split("Text geometry:\n")[1])
            chars = geometry[0]["characters"]
            content = "".join(c["text"] for c in chars)
            fields = {}
            for spec, value in zip(specs, values, strict=True):
                start = content.index(value)
                span = chars[start : start + len(value)]
                box = BBox(
                    x0=min(c["bbox"]["x0"] for c in span),
                    y0=min(c["bbox"]["y0"] for c in span),
                    x1=max(c["bbox"]["x1"] for c in span),
                    y1=max(c["bbox"]["y1"] for c in span),
                )
                fields[spec.path] = {
                    "kind": "scalar",
                    "value": value,
                    "value_type": spec.value_type,
                    "agreement": "single_reader",
                    "provenance": {
                        "page_index": 0,
                        "bbox": box.model_dump(),
                        "coordinate_space": "normalized_top_left",
                        "reader": "text_layer",
                        "document_hash": identity["source"]["document_hash"],
                    },
                }
            self.payload = {
                "source": identity["source"],
                "segment_id": identity["segment_id"],
                "fields": fields,
            }
            return ProviderResult(self.payload, "mock-observed-text")

        def vision_extract(self, request):
            calls.append("vision")
            payload = deepcopy(self.payload)
            for field in payload["fields"].values():
                field["provenance"]["reader"] = "vision"
            return ProviderResult(payload, "mock-observed-vision")

    monkeypatch.setattr(extract_segment, "make_provider", lambda *a, **k: ObservedProvider())
    accepted = post_files(
        api, {"job_ref": "ACTUAL-EXTRACTOR"}, docs(make_pdf([["COMMERCIAL INVOICE", *values]]))
    )
    drain(api)
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert body["status"] == "completed", body
    assert calls == ["text", "vision"]
    document = body["documents"][0]
    assert document["status"] == "review_required"
    assert document["fields"]["invoice.number"]["value"] == "INV-ACTUAL"
    assert document["fields"]["invoice.number"]["agreement"] == "unanimous"
    assert document["decision"]["value"] == "review"


def test_mixed_unreadable_segment_excludes_page_and_cannot_pass(api, monkeypatch):
    from trade_extract.db.models.page import Page
    from trade_extract.domain.provenance import ReaderKind
    from trade_extract.services.extraction import extract as actual_extract

    seen = []

    class RecordingReader:
        name = ReaderKind.TEXT_LAYER

        def read(self, provider, request, pages):
            seen.append(tuple(p.page_index for p in pages))
            return None

    def extract(provider, **kwargs):
        return actual_extract(provider, **kwargs, readers=(RecordingReader(),))

    monkeypatch.setattr(extract_segment, "extract", extract)
    accepted = post_files(
        api,
        {"job_ref": "MIXED-UNREADABLE"},
        docs(
            make_pdf(
                [
                    ["COMMERCIAL INVOICE", "Seller Buyer Invoice Total USD"],
                    ["Continuation goods and amounts", "Total 10 USD"],
                ]
            )
        ),
    )
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="prepare",
        kinds=["prepare_file"],
    )
    with api.session_factory() as session:
        page = session.scalar(select(Page).where(Page.page_index == 1))
        page.status = "unreadable"
        page.unreadable_reason = "blurred"
        session.commit()
    drain(api)
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert seen == [(0,)]
    assert body["documents"][0]["status"] == "unreadable"
    assert body["documents"][0]["decision"]["value"] == "unreadable"
    assert any("page:1:blurred" in d for d in body["documents"][0]["decision"]["diagnostics"])
    assert body["decision"]["value"] == "review"


def test_stale_extraction_owner_cannot_persist_or_complete_new_lease(api, tmp_path, monkeypatch):
    scripted_extraction(tmp_path, monkeypatch, api)
    scripted = extract_segment.extract

    def stolen(provider, **kwargs):
        outcome = scripted(provider, **kwargs)
        with api.session_factory() as session:
            task = session.scalar(select(JobTask).where(JobTask.kind == "extract_segment"))
            task.locked_by = "replacement-worker"
            task.attempts += 1
            session.commit()
        return outcome

    monkeypatch.setattr(extract_segment, "extract", stolen)
    accepted = post_files(
        api,
        {"job_ref": "STALE-LEASE"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Invoice Total USD"]])),
    )
    assert accepted.status_code == 202
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="prepare",
        kinds=["prepare_file"],
    )
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="old-worker",
        kinds=["extract_segment"],
    )
    with api.session_factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.kind == "extract_segment"))
        document = session.scalar(select(Document))
        assert task.status == "running" and task.locked_by == "replacement-worker"
        assert document.extraction is None and document.status == "processing"
        assert count_rows(api, "checks") == 0


def test_exhausted_extraction_waits_for_sibling_before_snapshot(api, tmp_path, monkeypatch):
    from trade_extract.db.models.file import File

    scripted_extraction(tmp_path, monkeypatch, api)
    scripted = extract_segment.extract

    def fail_one(provider, **kwargs):
        if kwargs["source"].filename == "document-0.pdf":
            raise RuntimeError("offline")
        return scripted(provider, **kwargs)

    monkeypatch.setattr(extract_segment, "extract", fail_one)
    accepted = post_files(
        api,
        {"job_ref": "SIBLING-FAILURE", "webhook_url": "http://127.0.0.1/hook"},
        docs(
            make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Invoice first"]]),
            make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Invoice second"]]),
        ),
    )
    for _ in range(2):
        assert run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=api.app.state.clock,
            worker_id="prepare",
            kinds=["prepare_file"],
        )
    with api.session_factory() as session:
        for task in session.scalars(select(JobTask).where(JobTask.kind == "extract_segment")):
            task.max_attempts = 1
            document = session.get(Document, task.document_id)
            file = session.get(File, document.file_id)
            task.priority = 1000 if file.filename == "document-0.pdf" else 0
        session.commit()
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="extract",
        kinds=["extract_segment"],
    )
    assert count_rows(api, "webhook_deliveries") == 0
    with api.session_factory() as session:
        states = list(session.scalars(select(Document.status)))
        assert sorted(states) == ["failed", "queued"]
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert body["status"] == "processing"
    drain(api)
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert body["status"] == "failed"
    assert {d["status"] for d in body["documents"]} == {"failed", "review_required"}
    with api.session_factory() as session:
        delivery = session.scalar(select(WebhookDelivery))
        assert delivery.payload["data"]["result"] == body


@pytest.mark.parametrize(
    "difference,check_status,conflicts", [("0.04", "pass", 0), ("0.06", "fail", 1)]
)
def test_effective_tolerance_reaches_checks_conflicts_and_recorded_policy(
    api, tmp_path, monkeypatch, difference, check_status, conflicts
):
    from decimal import Decimal

    with api.session_factory() as session:
        policy = session.scalar(select(Policy))
        policy.config = {
            "arithmetic_tolerance": "0.05",
            "invoice_pass_enabled": True,
            "review_depth": "checks",
        }
        session.commit()
    scripted_extraction(tmp_path, monkeypatch, api)
    scripted = extract_segment.extract
    position = 0

    def tolerance_case(provider, **kwargs):
        nonlocal position
        outcome = scripted(provider, **kwargs)
        document = outcome.document
        fields = dict(document.fields)
        delta = Decimal(difference)
        fields["totals.payable_amount"] = fields["totals.payable_amount"].model_copy(
            update={"value": str(Decimal("10") + delta)}
        )
        fields["shipment.gross_weight_kg"] = fields["totals.payable_amount"].model_copy(
            update={"value": str(Decimal("10") + (delta if position else 0))}
        )
        position += 1
        return ExtractionOutcome(
            "extracted", document=document.model_copy(update={"fields": fields})
        )

    monkeypatch.setattr(extract_segment, "extract", tolerance_case)
    accepted = post_files(
        api,
        {"job_ref": "TOLERANCE-BOUNDARY"},
        docs(*[make_pdf([["COMMERCIAL INVOICE", f"Seller Buyer Goods {i}"]]) for i in range(2)]),
    )
    drain(api)
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert len(body["conflicts"]) == conflicts
    for doc in body["documents"]:
        check = next(c for c in doc["checks"] if c["code"] == "INV-005")
        assert check["status"] == check_status
        assert doc["decision"]["value"] == ("pass" if check_status == "pass" else "review")
        assert doc["decision"]["policy_snapshot"]["arithmetic_tolerance"] == "0.05"
    assert body["decision"]["policy_snapshot"]["arithmetic_tolerance"] == "0.05"


def test_failed_first_prepare_waits_and_reconciles_later_invoice_conflict(
    api, tmp_path, monkeypatch
):
    from trade_extract.db.models.file import File

    scripted_extraction(tmp_path, monkeypatch, api, currencies=("USD", "EUR"))
    accepted = post_files(
        api,
        {"job_ref": "FAIL-FIRST-CONFLICT", "webhook_url": "http://127.0.0.1/hook"},
        docs(*[make_pdf([["COMMERCIAL INVOICE", f"Seller Buyer goods {i}"]]) for i in range(3)]),
    )
    with api.session_factory() as session:
        # Queue order is creation order; choose the matching earliest task directly.
        task = session.scalar(
            select(JobTask)
            .where(JobTask.kind == "prepare_file")
            .order_by(JobTask.created_at, JobTask.id)
        )
        first = session.get(File, task.payload["file_id"])
        (api.storage_dir / first.storage_key).unlink()
        task.max_attempts = 1
        task.priority = 1000
        session.commit()
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="prepare",
        kinds=["prepare_file"],
    )
    assert count_rows(api, "webhook_deliveries") == 0
    drain(api)
    body = api.client.get(f"/v1/results/{accepted.json()['result_id']}", headers=api.headers).json()
    assert body["status"] == "failed" and len(body["conflicts"]) == 1
    assert len(body["conflicts"][0]["sources"]) == 2
    assert body["review"] == {"extraction_open": 2, "conflict_open": 1}
    with api.session_factory() as session:
        delivery = session.scalar(select(WebhookDelivery))
        assert delivery.payload["data"]["result"] == body


def test_duplicate_only_submission_preserves_retained_reviews_and_conflicts(
    api, tmp_path, monkeypatch
):
    from trade_extract.db.models.job import Job

    scripted_extraction(tmp_path, monkeypatch, api, currencies=("USD", "EUR"))
    payloads = [make_pdf([["COMMERCIAL INVOICE", f"Seller Buyer Goods {i}"]]) for i in range(2)]
    first = post_files(api, {"job_ref": "DUPLICATE-REVIEW"}, docs(*payloads))
    drain(api)
    second = post_files(
        api,
        {"job_ref": "DUPLICATE-REVIEW", "webhook_url": "http://127.0.0.1/hook"},
        docs(*payloads),
    )
    assert second.status_code == 202
    body = api.client.get(f"/v1/results/{second.json()['result_id']}", headers=api.headers).json()
    assert body["status"] == "completed" and body["decision"]["value"] == "review"
    assert body["decision"]["route"] == "conflict" and len(body["conflicts"]) == 1
    assert body["review"] == {"extraction_open": 2, "conflict_open": 1}
    with api.session_factory() as session:
        assert session.get(Job, first.json()["job_id"]).status == "review_required"
        assert session.scalar(select(WebhookDelivery)).event_type == "result.review_required"


def test_prior_job_document_failure_is_not_lost_in_later_submission(api, tmp_path, monkeypatch):
    monkeypatch.setattr(
        extract_segment,
        "extract",
        lambda *args, **kwargs: ExtractionOutcome("extraction_failed", errors=("offline",)),
    )
    first = post_files(
        api,
        {"job_ref": "RETAINED-FAILURE"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer first"]])),
    )
    drain(api)
    scripted_extraction(tmp_path, monkeypatch, api)
    second = post_files(
        api,
        {"job_ref": "RETAINED-FAILURE"},
        docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer second"]])),
    )
    assert second.status_code == 202
    drain(api)
    for result_id in (first.json()["result_id"], second.json()["result_id"]):
        body = api.client.get(f"/v1/results/{result_id}", headers=api.headers).json()
        assert body["decision"]["value"] == "failed"
