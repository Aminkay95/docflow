from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import ApiHarness, count_rows
from tests.support import make_image, make_multipage_tiff, make_pdf, prepare_temp_snapshot
from trade_extract.db.models.document import Document
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.db.models.segment import Segment
from trade_extract.db.models.submission import Submission
from trade_extract.db.models.task import JobTask
from trade_extract.db.repositories import segments as segments_repo
from trade_extract.db.repositories import tasks as tasks_repo
from trade_extract.domain.clock import FrozenClock
from trade_extract.domain.ids import new_id
from trade_extract.services.storage import build_storage
from trade_extract.workers.runner import run_once

pytestmark = pytest.mark.integration


def post_files(api: ApiHarness, *, job_ref: str, files: list[tuple]):
    return api.client.post(
        "/v1/extract", headers=api.headers, data={"job_ref": job_ref}, files=files
    )


def pdfs(*payloads: bytes, name: str = "document") -> list[tuple]:
    return [
        ("documents", (f"{name}-{index}.pdf", payload, "application/pdf"))
        for index, payload in enumerate(payloads)
    ]


def images(payload: bytes, filename: str, content_type: str) -> list[tuple]:
    return [("documents", (filename, payload, content_type))]


def drain(api: ApiHarness, *, max_loops: int = 80) -> int:
    for processed in range(max_loops):
        if not run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=api.app.state.clock,
            worker_id="pages-worker",
        ):
            return processed
    raise AssertionError("worker did not drain")


def poll(api: ApiHarness, result_id: str) -> dict:
    response = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert response.status_code == 200, response.text
    return response.json()


def documents_in_page_order(body: dict) -> list[dict]:
    return sorted(body["documents"], key=lambda document: document["source"]["page_start"])


def test_mixed_pdf_yields_three_documents_covering_every_page(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-MIXED",
        files=pdfs(
            make_pdf(
                [
                    ["BILL OF LADING"],
                    ["COMMERCIAL INVOICE"],
                    ["PACKING LIST"],
                ]
            ),
            name="mixed",
        ),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)

    body = poll(api, result_id)
    assert body["status"] == "completed"
    assert body["progress"]["documents_total"] == 3
    assert body["progress"]["documents_completed"] == 3
    assert body["progress"]["pages_total"] == 3
    assert body["progress"]["pages_read"] == 3

    documents = documents_in_page_order(body)
    assert [
        (
            document["document_type"],
            document["source"]["page_start"],
            document["source"]["page_end"],
            document["source"]["page_count"],
        )
        for document in documents
    ] == [
        ("bill_of_lading", 0, 0, 1),
        ("commercial_invoice", 1, 1, 1),
        ("packing_list", 2, 2, 1),
    ]

    with api.session_factory() as session:
        file_row = session.execute(select(File)).scalars().one()
        file_sha = file_row.sha256
        for document in documents:
            assert document["status"] == "queued"
            assert 0.0 <= document["document_type_confidence"] <= 1.0
            assert document["fields"] is None
            assert document["tables"] == {}
            assert document["extension"] == {}
            assert document["checks"] is None
            assert document["decision"] is None
            assert document["source"]["filename"].startswith("mixed-")
            assert document["source"]["file_hash"] == f"sha256:{file_sha}"
            expected = hashlib.sha256(
                (
                    f"{file_sha}:{document['source']['page_start']}:"
                    f"{document['source']['page_end']}:{document['document_type']}"
                ).encode()
            ).hexdigest()
            assert document["source"]["document_hash"] == f"sha256:{expected}"


def test_every_page_has_a_decodable_image_and_thumbnail(api: ApiHarness) -> None:
    # Non-default rendering settings: a handler that hardcodes the defaults fails here.
    api.settings.page_render_dpi = 120
    api.settings.page_thumbnail_max_px = 96
    before = prepare_temp_snapshot()
    accepted = post_files(
        api,
        job_ref="BL-PAGE-ATOMS",
        files=pdfs(make_pdf([["BILL OF LADING"], ["COMMERCIAL INVOICE"]])),
    )
    assert accepted.status_code == 202, accepted.text
    drain(api)
    assert prepare_temp_snapshot() <= before

    storage = build_storage(api.settings)
    with api.session_factory() as session:
        pages = session.execute(select(Page).order_by(Page.page_index)).scalars().all()
        segments = session.execute(select(Segment).order_by(Segment.page_start)).scalars().all()
        documents = session.execute(select(Document)).scalars().all()

    assert len(pages) == 2
    assert len(segments) == 2
    assert len(documents) == 2
    assert {segment.id for segment in segments} == {document.segment_id for document in documents}

    covered = [
        index for segment in segments for index in range(segment.page_start, segment.page_end + 1)
    ]
    assert covered == list(range(len(pages)))

    for page in pages:
        assert page.status == "pending"
        assert page.width_px > 0 and page.height_px > 0
        assert page.dpi == api.settings.page_render_dpi
        assert page.image_key and page.thumbnail_key
        assert page.text_layer_present is True
        assert page.text_layer and page.text_layer.strip()
        with storage.get(page.image_key) as handle, Image.open(handle) as image:
            assert image.format == "JPEG"
            assert image.width > 0 and image.height > 0
            image_long_edge = max(image.size)
        with storage.get(page.thumbnail_key) as handle, Image.open(handle) as thumbnail:
            assert thumbnail.format == "JPEG"
            assert max(thumbnail.size) <= api.settings.page_thumbnail_max_px
            assert max(thumbnail.size) < image_long_edge


def test_page_image_quality_setting_changes_the_stored_image(api: ApiHarness) -> None:
    payload = make_pdf([["COMMERCIAL INVOICE", "Line item 1", "Widget 10.00"]])
    lengths: dict[int, int] = {}
    known_pages: set[str] = set()

    for job_ref, quality in (("BL-QUALITY-LOW", 20), ("BL-QUALITY-HIGH", 95)):
        api.settings.page_image_quality = quality
        accepted = post_files(api, job_ref=job_ref, files=pdfs(payload))
        assert accepted.status_code == 202, accepted.text
        drain(api)

        with api.session_factory() as session:
            page = next(
                row for row in session.execute(select(Page)).scalars() if row.id not in known_pages
            )
        known_pages.add(page.id)
        storage = build_storage(api.settings)
        with storage.get(page.image_key) as handle:
            lengths[quality] = len(handle.read())

    assert lengths[20] < lengths[95]


def test_multipage_invoice_is_one_segment(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-MULTIPAGE",
        files=pdfs(
            make_pdf(
                [
                    ["BILL OF LADING"],
                    ["COMMERCIAL INVOICE"],
                    ["Line item 1", "Widget 10.00"],
                    ["PACKING LIST"],
                ]
            )
        ),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)

    body = poll(api, result_id)
    documents = documents_in_page_order(body)
    assert len(documents) == 3
    invoice = documents[1]
    assert invoice["document_type"] == "commercial_invoice"
    assert invoice["source"]["page_start"] == 1
    assert invoice["source"]["page_end"] == 2
    assert invoice["source"]["page_count"] == 2


def test_image_only_file_is_one_unknown_document(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-IMAGE-ONLY",
        files=images(make_image("JPEG"), "scan.jpg", "image/jpeg"),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)

    body = poll(api, result_id)
    assert body["status"] == "completed"
    assert len(body["documents"]) == 1
    document = body["documents"][0]
    assert document["document_type"] == "unknown"
    assert document["source"]["page_start"] == 0
    assert document["source"]["page_end"] == 0

    with api.session_factory() as session:
        page = session.execute(select(Page)).scalars().one()
    assert page.text_layer_present is False
    assert page.text_layer_chars == 0
    assert page.text_layer in ("", None)


def test_multiframe_tiff_is_three_pages_and_one_unknown_segment(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-TIFF-SEG",
        files=images(make_multipage_tiff(3), "multi.tiff", "image/tiff"),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)

    body = poll(api, result_id)
    assert len(body["documents"]) == 1
    document = body["documents"][0]
    assert document["document_type"] == "unknown"
    assert (document["source"]["page_start"], document["source"]["page_end"]) == (0, 2)
    assert document["source"]["page_count"] == 3
    assert count_rows(api, "pages") == 3


def test_rearmed_prepare_is_idempotent(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-IDEMPOTENT",
        files=pdfs(make_pdf([["COMMERCIAL INVOICE"]])),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)

    before = poll(api, result_id)
    before_ids = [document["document_id"] for document in before["documents"]]
    before_counts = {table: count_rows(api, table) for table in ("pages", "segments", "documents")}

    with api.session_factory() as session:
        task = (
            session.execute(select(JobTask).where(JobTask.kind == "prepare_file")).scalars().one()
        )
        tasks_repo.rearm(
            session,
            dedupe_key=task.dedupe_key,
            job_id=task.job_id,
            submission_id=task.submission_id,
            kind=task.kind,
            payload=dict(task.payload),
            now=api.app.state.clock.now(),
        )
        session.commit()

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="idempotent-worker",
        kinds=["prepare_file"],
    )

    after = poll(api, result_id)
    assert [document["document_id"] for document in after["documents"]] == before_ids
    assert {
        table: count_rows(api, table) for table in ("pages", "segments", "documents")
    } == before_counts


def test_rearmed_prepare_is_a_no_op_while_a_sibling_is_active(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-IDEMPOTENT-SIBLING",
        files=pdfs(make_pdf([["COMMERCIAL INVOICE"]]), make_pdf([["PACKING LIST"]])),
    )
    assert accepted.status_code == 202, accepted.text
    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="sibling-worker",
        kinds=["prepare_file"],
    )

    with api.session_factory() as session:
        submission = session.execute(select(Submission)).scalars().one()
        assert submission.status == "processing"
        document = session.execute(select(Document)).scalars().one()
        prepared_file_id = document.file_id
        before_ids = [
            row.id
            for row in session.execute(
                select(Document).where(Document.file_id == prepared_file_id)
            ).scalars()
        ]
        tasks = (
            session.execute(select(JobTask).where(JobTask.kind == "prepare_file")).scalars().all()
        )
        target = next(
            task for task in tasks if (task.payload or {}).get("file_id") == prepared_file_id
        )
        sibling = next(
            task for task in tasks if (task.payload or {}).get("file_id") != prepared_file_id
        )
        # Isolate the re-armed task so the still-unprepared sibling cannot be claimed.
        sibling.run_after = api.app.state.clock.now() + timedelta(hours=1)
        tasks_repo.rearm(
            session,
            dedupe_key=target.dedupe_key,
            job_id=target.job_id,
            submission_id=target.submission_id,
            kind=target.kind,
            payload=dict(target.payload),
            now=api.app.state.clock.now(),
        )
        session.commit()

    before_counts = {table: count_rows(api, table) for table in ("pages", "segments", "documents")}

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="sibling-worker",
        kinds=["prepare_file"],
    )

    with api.session_factory() as session:
        assert session.execute(select(Submission)).scalars().one().status == "processing"
        after_ids = [
            row.id
            for row in session.execute(
                select(Document).where(Document.file_id == prepared_file_id)
            ).scalars()
        ]
    assert after_ids == before_ids
    assert {
        table: count_rows(api, table) for table in ("pages", "segments", "documents")
    } == before_counts


def test_progress_counts_a_sibling_that_is_still_waiting(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-PROGRESS-MIDFLIGHT",
        files=pdfs(make_pdf([["COMMERCIAL INVOICE"]]), make_pdf([["PACKING LIST"]])),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=api.app.state.clock,
        worker_id="midflight-worker",
        kinds=["prepare_file"],
    )

    body = poll(api, result_id)
    assert body["status"] == "processing"
    assert body["progress"]["documents_total"] == 2
    assert body["progress"]["documents_completed"] == 1
    assert body["progress"]["documents_failed"] == 0

    drain(api)
    body = poll(api, result_id)
    assert body["status"] == "completed"
    assert body["progress"]["documents_total"] == 2
    assert body["progress"]["documents_completed"] == 2
    assert body["progress"]["documents_failed"] == 0


def test_partial_failure_reports_honest_progress(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    accepted = post_files(
        api,
        job_ref="BL-PARTIAL-FAIL",
        files=pdfs(make_pdf([["COMMERCIAL INVOICE"]]), make_pdf([["PACKING LIST"]])),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]

    assert run_once(
        settings=api.settings,
        session_factory_=api.session_factory,
        clock=clock,
        worker_id="partial-worker",
        kinds=["prepare_file"],
    )
    with api.session_factory() as session:
        files = session.execute(select(File)).scalars().all()
        prepared = next(row for row in files if row.status == "processed")
        missing = next(row for row in files if row.status in ("received", "processing"))
        missing_file_id = missing.id
        missing_filename = missing.filename
        missing_storage_key = missing.storage_key
    (api.storage_dir / missing_storage_key).unlink()

    for _ in range(3):
        assert run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="partial-worker",
            kinds=["prepare_file"],
        )
        clock.advance(seconds=120)

    with api.session_factory() as session:
        failed_task = next(
            task
            for task in session.execute(
                select(JobTask).where(JobTask.kind == "prepare_file")
            ).scalars()
            if (task.payload or {}).get("file_id") == missing_file_id
        )
        assert failed_task.status == "dead"

    body = poll(api, result_id)
    assert body["status"] == "failed"
    assert body["progress"]["documents_total"] == 2
    assert body["progress"]["documents_completed"] == 1
    assert body["progress"]["documents_failed"] == 1
    assert [document["source"]["filename"] for document in body["documents"]] == [prepared.filename]
    assert any(missing_filename in reason for reason in body["decision"]["reasons"])


def test_resurrected_file_replaces_the_prior_page_set(api: ApiHarness) -> None:
    payload = make_pdf([["COMMERCIAL INVOICE"]])
    first = post_files(api, job_ref="BL-RESURRECT", files=pdfs(payload))
    assert first.status_code == 202, first.text
    drain(api)

    with api.session_factory() as session:
        old_document_id = session.execute(select(Document.id)).scalar_one()
        job_id = session.execute(select(Job.id)).scalar_one()

    with api.session_factory() as session:
        session.execute(
            text(
                "UPDATE files SET status = 'failed', error_code = 'boom', "
                "error_message = 'processing exploded'"
            )
        )
        job = session.get(Job, job_id)
        assert job is not None
        job.status = "processing"
        job.terminal_at = None
        job.terminal_reason = None
        session.commit()

    second = post_files(api, job_ref="BL-RESURRECT", files=pdfs(payload))
    assert second.status_code == 202, second.text
    assert second.json()["duplicate_documents"] == 0
    drain(api)

    with api.session_factory() as session:
        document_ids = session.execute(select(Document.id)).scalars().all()
    assert len(document_ids) == 1
    assert document_ids[0] != old_document_id
    assert count_rows(api, "pages") == 1
    assert count_rows(api, "segments") == 1
    assert count_rows(api, "documents") == 1

    body = poll(api, second.json()["result_id"])
    assert body["status"] == "completed"
    assert len(body["documents"]) == 1


def test_render_failure_is_terminal_and_names_the_file(api: ApiHarness) -> None:
    clock = FrozenClock(datetime.now(UTC))
    api.app.state.clock = clock
    accepted = post_files(
        api,
        job_ref="BL-RENDER-FAIL",
        files=pdfs(make_pdf([["COMMERCIAL INVOICE"]]), name="unreadable.pdf"),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]

    with api.session_factory() as session:
        storage_key = session.execute(select(File.storage_key)).scalar_one()
    (api.storage_dir / storage_key).unlink()

    before = prepare_temp_snapshot()
    for _ in range(3):
        assert run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=clock,
            worker_id="failing-pages-worker",
            kinds=["prepare_file"],
        )
        clock.advance(seconds=120)

    with api.session_factory() as session:
        task = (
            session.execute(select(JobTask).where(JobTask.kind == "prepare_file")).scalars().one()
        )
        assert task.status == "dead"
    assert count_rows(api, "pages") == 0
    assert count_rows(api, "segments") == 0
    assert count_rows(api, "documents") == 0
    assert prepare_temp_snapshot() <= before

    body = poll(api, result_id)
    assert body["status"] == "failed"
    assert body["documents"] == []
    assert body["decision"]["value"] == "failed"
    assert any("unreadable.pdf" in reason for reason in body["decision"]["reasons"])


def test_document_filter_returns_one_or_none(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-DOC-FILTER-SEG",
        files=pdfs(make_pdf([["BILL OF LADING"], ["COMMERCIAL INVOICE"]])),
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)
    documents = poll(api, result_id)["documents"]
    target = documents[0]["document_id"]

    one = api.client.get(
        f"/v1/results/{result_id}?include=documents&document_id={target}",
        headers=api.headers,
    )
    assert one.status_code == 200
    assert [document["document_id"] for document in one.json()["documents"]] == [target]

    missing = api.client.get(
        f"/v1/results/{result_id}?include=documents&document_id=doc_does_not_exist",
        headers=api.headers,
    )
    assert missing.status_code == 200
    assert missing.json()["documents"] == []

    without_documents = api.client.get(
        f"/v1/results/{result_id}?include=conflicts", headers=api.headers
    )
    assert without_documents.status_code == 200
    assert without_documents.json()["documents"] == []


def test_duplicate_file_is_not_relisted_for_the_new_submission(api: ApiHarness) -> None:
    payload = make_pdf([["COMMERCIAL INVOICE"]])
    first = post_files(api, job_ref="BL-DUPLICATE-SEG", files=pdfs(payload))
    assert first.status_code == 202, first.text
    drain(api)
    counts = {table: count_rows(api, table) for table in ("pages", "segments", "documents")}

    second = post_files(api, job_ref="BL-DUPLICATE-SEG", files=pdfs(payload))
    assert second.status_code == 202, second.text
    assert second.json()["duplicate_documents"] == 1
    drain(api)

    body = poll(api, second.json()["result_id"])
    assert body["status"] == "completed"
    assert body["documents"] == []
    assert {table: count_rows(api, table) for table in ("pages", "segments", "documents")} == counts


def test_duplicate_parts_count_toward_progress(api: ApiHarness) -> None:
    duplicate_payload = make_pdf([["COMMERCIAL INVOICE"]])
    first = post_files(api, job_ref="BL-DUPLICATE-PROGRESS", files=pdfs(duplicate_payload))
    assert first.status_code == 202, first.text
    drain(api)

    second = post_files(
        api,
        job_ref="BL-DUPLICATE-PROGRESS",
        files=pdfs(duplicate_payload, make_pdf([["PACKING LIST"]])),
    )
    assert second.status_code == 202, second.text
    assert second.json()["duplicate_documents"] == 1
    drain(api)

    body = poll(api, second.json()["result_id"])
    assert body["status"] == "completed"
    assert [document["document_type"] for document in body["documents"]] == ["packing_list"]
    assert body["progress"]["documents_total"] == 2
    assert body["progress"]["documents_completed"] == 2
    assert body["progress"]["documents_failed"] == 0


def test_segmentation_floor_is_read_from_settings_at_a_non_default_value(
    api: ApiHarness,
) -> None:
    payload = make_pdf(
        [
            [
                "COMMERCIAL INVOICE",
                "Reference",
                "Document",
                "Page one",
                "PACKING LIST",
                "Cartons",
            ]
        ]
    )
    first = post_files(api, job_ref="BL-FLOOR-DEFAULT", files=pdfs(payload))
    assert first.status_code == 202, first.text
    drain(api)
    typed = poll(api, first.json()["result_id"])["documents"][0]
    assert typed["document_type"] == "commercial_invoice"
    assert 0.70 <= typed["document_type_confidence"] <= 0.80

    api.settings.segmentation_min_confidence = 0.9
    second = post_files(api, job_ref="BL-FLOOR-HIGH", files=pdfs(payload))
    assert second.status_code == 202, second.text
    drain(api)
    unknown = poll(api, second.json()["result_id"])["documents"][0]
    assert unknown["document_type"] == "unknown"


def test_overlapping_segments_are_rejected_by_the_database(api: ApiHarness) -> None:
    accepted = post_files(
        api,
        job_ref="BL-OVERLAP",
        files=pdfs(make_pdf([["COMMERCIAL INVOICE"]])),
    )
    assert accepted.status_code == 202, accepted.text
    drain(api)

    with api.session_factory() as session:
        file_row = session.execute(select(File)).scalars().one()
        segments_repo.insert(
            session,
            segment_id=new_id("seg_"),
            file_id=file_row.id,
            job_id=file_row.job_id,
            page_start=10,
            page_end=10,
            document_type="unknown",
            confidence=0.0,
            fingerprint="overlap-a",
            now=api.app.state.clock.now(),
        )
        session.flush()
        segments_repo.insert(
            session,
            segment_id=new_id("seg_"),
            file_id=file_row.id,
            job_id=file_row.job_id,
            page_start=10,
            page_end=10,
            document_type="unknown",
            confidence=0.0,
            fingerprint="overlap-b",
            now=api.app.state.clock.now(),
        )
        with pytest.raises(IntegrityError) as caught:
            session.flush()
        diagnostic = getattr(getattr(caught.value, "orig", None), "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        assert constraint_name == "ex_segments_page_range" or (
            "ex_segments_page_range" in str(caught.value)
        )
        session.rollback()

    assert count_rows(api, "segments") == 1


def test_filename_does_not_decide_the_document_type(api: ApiHarness) -> None:
    payload = make_pdf([["BILL OF LADING", "Shipper: ACME"]])
    accepted = post_files(
        api,
        job_ref="BL-FILENAME-LIE",
        files=[("documents", ("invoice.pdf", payload, "application/pdf"))],
    )
    assert accepted.status_code == 202, accepted.text
    result_id = accepted.json()["result_id"]
    drain(api)

    document = poll(api, result_id)["documents"][0]
    assert document["source"]["filename"] == "invoice.pdf"
    assert document["document_type"] == "bill_of_lading"
