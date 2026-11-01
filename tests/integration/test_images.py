from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration.conftest import ApiHarness, count_rows
from tests.support import (
    JPEG_BYTES,
    make_image,
    make_multipage_tiff,
    make_pdf,
    truncated_image,
)

pytestmark = pytest.mark.integration


def storage_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def assert_nothing_persisted(api: ApiHarness) -> None:
    assert count_rows(api, "jobs") == 0
    assert count_rows(api, "submissions") == 0
    assert count_rows(api, "files") == 0
    assert count_rows(api, "job_tasks") == 0
    assert storage_files(api.storage_dir) == []


def post_files(api: ApiHarness, files: list[tuple], data: dict[str, str] | None = None):
    return api.client.post(
        "/v1/extract",
        headers=api.headers,
        data=data or {"job_ref": "BL-IMG"},
        files=files,
    )


def test_jpeg_is_accepted(api: ApiHarness) -> None:
    response = post_files(api, [("documents", ("scan.jpg", JPEG_BYTES, "image/jpeg"))])
    assert response.status_code == 202, response.text
    assert response.json()["submitted_documents"] == 1
    with api.session_factory() as session:
        row = session.execute(text("SELECT content_type, page_count FROM files")).mappings().one()
    assert row["content_type"] == "image/jpeg"
    assert row["page_count"] == 1


def test_png_and_webp_are_decoded_not_assumed(api: ApiHarness) -> None:
    for fmt, filename, content_type in (
        ("PNG", "scan.png", "image/png"),
        ("WEBP", "scan.webp", "image/webp"),
    ):
        response = post_files(
            api,
            [("documents", (filename, make_image(fmt), content_type))],
            {"job_ref": f"BL-{fmt}"},
        )
        assert response.status_code == 202, response.text


def test_truncated_jpeg_is_rejected_before_storage(api: ApiHarness) -> None:
    response = post_files(
        api,
        [("documents", ("truncated.jpg", truncated_image(), "image/jpeg"))],
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "malformed_document"
    assert "Re-export" in error["message"]
    assert_nothing_persisted(api)


def test_multi_page_tiff_counts_every_frame(api: ApiHarness) -> None:
    response = post_files(
        api,
        [("documents", ("multi.tiff", make_multipage_tiff(3), "image/tiff"))],
        {"job_ref": "BL-TIFF"},
    )
    assert response.status_code == 202, response.text
    result_id = response.json()["result_id"]

    with api.session_factory() as session:
        page_count = session.execute(text("SELECT page_count FROM files")).scalar_one()
    assert page_count == 3

    polled = api.client.get(f"/v1/results/{result_id}", headers=api.headers)
    assert polled.status_code == 200
    assert polled.json()["progress"]["pages_total"] == 3


def test_multi_page_tiff_respects_the_page_cap(api: ApiHarness) -> None:
    api.settings.max_pages_per_file = 2
    response = post_files(
        api,
        [("documents", ("multi.tiff", make_multipage_tiff(3), "image/tiff"))],
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "page_limit_exceeded"
    assert error["details"]["file_pages"] == 3
    assert_nothing_persisted(api)


def test_pdf_with_no_text_layer_still_proceeds_past_the_shipment_guard(
    api: ApiHarness,
) -> None:
    image_only_pdf = make_pdf([[""]])
    response = post_files(api, [("documents", ("scan.pdf", image_only_pdf, "application/pdf"))])
    assert response.status_code == 202, response.text
