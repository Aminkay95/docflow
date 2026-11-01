from __future__ import annotations

import asyncio

import pytest

from tests.support import (
    DOCX_BYTES,
    JPEG_BYTES,
    cleanup_raw,
    make_encrypted_pdf,
    make_multipage_tiff,
    make_pdf,
    raw_submission,
    temp_snapshot,
    truncated_image,
)
from trade_extract.domain import errors
from trade_extract.services.limits import (
    detect_media_type,
    extract_bill_of_lading_numbers,
    normalize_bill_of_lading,
    spool_submission,
    validate_idempotency_key,
    validate_job_ref,
    validate_submission,
    validate_webhook_url,
)
from trade_extract.services.page_model import probe_container


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (b"%PDF-1.7", "pdf"),
        (b"\xff\xd8\xff\xe0", "jpeg"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"RIFF\x00\x00\x00\x00WEBP", "webp"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
        (b"PK\x03\x04", None),
        (b"GIF89a", None),
    ],
)
def test_magic_byte_detection(header: bytes, expected: str | None) -> None:
    assert detect_media_type(header) == expected


def test_job_ref_validation(unit_settings) -> None:
    assert validate_job_ref(None, unit_settings) is None
    assert validate_job_ref("BL-2026-00417", unit_settings) == "BL-2026-00417"
    for bad in ("", "x" * 129, "caf\u00e9", "line\nbreak"):
        with pytest.raises(errors.DomainError) as caught:
            validate_job_ref(bad, unit_settings)
        assert caught.value.code == "invalid_request"
        assert caught.value.details["field"] == "job_ref"


def test_idempotency_key_validation(unit_settings) -> None:
    assert validate_idempotency_key(None, unit_settings) is None
    assert validate_idempotency_key("12345678", unit_settings) == "12345678"
    for bad in ("short", "k" * 129, "tab\tkey12"):
        with pytest.raises(errors.DomainError) as caught:
            validate_idempotency_key(bad, unit_settings)
        assert caught.value.code == "invalid_request"
        assert caught.value.details["field"] == "Idempotency-Key"


def test_webhook_url_validation(unit_settings) -> None:
    assert (
        validate_webhook_url("https://hooks.example.test/ingest", unit_settings)
        == "https://hooks.example.test/ingest"
    )
    assert (
        validate_webhook_url("http://localhost:9000/hook", unit_settings)
        == "http://localhost:9000/hook"
    )
    assert validate_webhook_url("", unit_settings) is None
    assert validate_webhook_url("   ", unit_settings) is None
    for bad in (
        "not a url",
        "ftp://example.test/hook",
        "https://" + "a" * 2050,
        "https://example.test/hook with space",
        "https://user:secret@example.test/hook",
    ):
        with pytest.raises(errors.DomainError) as caught:
            validate_webhook_url(bad, unit_settings)
        assert caught.value.code == "invalid_request"
        assert caught.value.details["field"] == "webhook_url"
        assert "value" not in caught.value.details
        assert "secret" not in str(caught.value.details)


def test_webhook_url_requires_https_in_production(unit_settings) -> None:
    production = unit_settings.model_copy(update={"environment": "production"})
    with pytest.raises(errors.DomainError) as caught:
        validate_webhook_url("http://hooks.example.test/ingest", production)
    assert "HTTPS" in caught.value.message


def test_webhook_url_requires_https_outside_development(unit_settings) -> None:
    test_environment = unit_settings.model_copy(update={"environment": "test"})
    with pytest.raises(errors.DomainError) as caught:
        validate_webhook_url("http://hooks.example.test/ingest", test_environment)
    assert "HTTPS" in caught.value.message
    assert (
        validate_webhook_url("https://hooks.example.test/ingest", test_environment)
        == "https://hooks.example.test/ingest"
    )


def test_bill_of_lading_normalisation() -> None:
    assert normalize_bill_of_lading("KEMU-123 456.789") == "KEMU123456789"
    assert normalize_bill_of_lading("kem u 123456789") == "KEMU123456789"


def test_multi_shipment_evidence_requires_label_and_token() -> None:
    two = [
        "BILL OF LADING\nB/L No: KEMU-123456789\nInvoice 42",
        "OCEAN BILL OF LADING\nB/L KEMU987654321",
    ]
    assert extract_bill_of_lading_numbers(two) == ["KEMU123456789", "KEMU987654321"]

    one = ["BILL OF LADING\nB/L No: KEMU123456789", "COMMERCIAL INVOICE\nB/L KEMU123456789"]
    assert extract_bill_of_lading_numbers(one) == ["KEMU123456789"]

    # Scanning stops at the first slash: the second token is not evidence.
    slash_separated = ["BILL OF LADING\nB/L KEMU123456789/KEMU987654321"]
    assert extract_bill_of_lading_numbers(slash_separated) == ["KEMU123456789"]

    assert extract_bill_of_lading_numbers(["BILL OF LADING\nno number here"]) == []
    assert extract_bill_of_lading_numbers(["KEMU123456789\nKEMU987654321"]) == []
    # A candidate needs at least one letter and at least three digits.
    assert extract_bill_of_lading_numbers(["BILL OF LADING\nB/L 123456"]) == []
    assert extract_bill_of_lading_numbers(["BILL OF LADING\nB/L AB12"]) == []
    assert extract_bill_of_lading_numbers(["BILL OF LADING\nB/L AB123"]) == ["AB123"]


def test_bill_of_lading_only_takes_the_first_token_after_the_label() -> None:
    lines = [
        "B/L NO. KEMU123456789 DATE 2026-10-31 SEAL S-998877 INVOICE INV-424242",
        "WAYBILL # MSCU7654321",
        "OCEAN BILL OF LADING: OOLU1122334",
        "BILL OF LADING",
    ]
    assert extract_bill_of_lading_numbers(["\n".join(lines)]) == [
        "KEMU123456789",
        "MSCU7654321",
        "OOLU1122334",
    ]

    # A label whose first token is a date, a seal or a word is not evidence.
    assert extract_bill_of_lading_numbers(["BILL OF LADING Date: 2026-10-31"]) == []
    assert extract_bill_of_lading_numbers(["B/L Seal S-998877 INV-424242"]) == []
    assert extract_bill_of_lading_numbers(["B/L No KEMU123456789"]) == ["KEMU123456789"]
    # An invoice or container number on the line after the number is not evidence.
    assert extract_bill_of_lading_numbers(
        ["B/L KEMU123456789\nCONTAINER MSKU1234567\nINVOICE INV-424242"]
    ) == ["KEMU123456789"]


def test_valid_submission_proceeds(unit_settings) -> None:
    raw = raw_submission(make_pdf([["INVOICE"], ["PACKING LIST"]]), job_ref="BL-1")
    try:
        validated = validate_submission(raw, unit_settings)
        assert validated.total_pages == 2
        assert validated.job_ref == "BL-1"
        assert validated.documents[0].page_count == 2
        assert validated.bill_of_lading_numbers == []
    finally:
        cleanup_raw(raw)


def test_image_only_submission_proceeds(unit_settings) -> None:
    raw = raw_submission(JPEG_BYTES, filenames=["scan.jpg"])
    try:
        validated = validate_submission(raw, unit_settings)
        assert validated.total_pages == 1
        assert validated.documents[0].pages[0].text == ""
    finally:
        cleanup_raw(raw)


def test_zero_documents_rejected(unit_settings) -> None:
    raw = raw_submission(job_ref="BL-1")
    try:
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, unit_settings)
        assert caught.value.code == "no_documents"
        assert "at most 20" in caught.value.message
    finally:
        cleanup_raw(raw)


def test_group_limit_rejected(unit_settings) -> None:
    raw = raw_submission(*([make_pdf()] * 2))
    try:
        limiter = unit_settings.model_copy(update={"max_documents_per_group": 1})
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, limiter)
        assert caught.value.code == "too_many_documents"
        assert caught.value.details == {"limit": 1, "actual": 2}
    finally:
        cleanup_raw(raw)


def test_per_file_page_limit(unit_settings) -> None:
    raw = raw_submission(make_pdf([["ONE"], ["TWO"]]))
    try:
        limiter = unit_settings.model_copy(update={"max_pages_per_file": 1})
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, limiter)
        assert caught.value.code == "page_limit_exceeded"
        assert caught.value.details["file_pages"] == 2
    finally:
        cleanup_raw(raw)


def test_submission_page_limit(unit_settings) -> None:
    raw = raw_submission(make_pdf([["ONE"], ["TWO"]]), make_pdf([["THREE"], ["FOUR"]]))
    try:
        limiter = unit_settings.model_copy(
            update={"max_pages_per_file": 5, "max_pages_per_submission": 3}
        )
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, limiter)
        assert caught.value.code == "page_limit_exceeded"
        assert caught.value.details["submission_pages"] == 4
        assert caught.value.details["limits"]["max_pages_per_submission"] == 3
    finally:
        cleanup_raw(raw)


def test_encrypted_pdf_rejected(unit_settings) -> None:
    raw = raw_submission(make_encrypted_pdf(), filenames=["locked.pdf"])
    try:
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, unit_settings)
        assert caught.value.code == "encrypted_document"
        assert "Remove the password" in caught.value.message
    finally:
        cleanup_raw(raw)


def test_malformed_pdf_rejected(unit_settings) -> None:
    raw = raw_submission(b"%PDF-1.4\nthis is not a pdf", filenames=["broken.pdf"])
    try:
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, unit_settings)
        assert caught.value.code == "malformed_document"
    finally:
        cleanup_raw(raw)


def test_docx_magic_bytes_are_not_an_accepted_container() -> None:
    # DOCX never reaches probe_container: the streamed magic-byte check is earlier.
    assert detect_media_type(DOCX_BYTES[:16]) is None


def test_multi_shipment_rejected(unit_settings) -> None:
    raw = raw_submission(
        make_pdf(
            [
                [
                    "BILL OF LADING",
                    "B/L No: KEMU123456789",
                    "COMMERCIAL INVOICE",
                    "B/L KEMU987654321",
                ]
            ]
        )
    )
    try:
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, unit_settings)
        assert caught.value.code == "multi_shipment_container"
        assert caught.value.details["shipment_count"] == 2
        assert caught.value.details["bill_of_lading_numbers"] == [
            "KEMU123456789",
            "KEMU987654321",
        ]
        assert "one shipment per job_ref" in caught.value.message
    finally:
        cleanup_raw(raw)


def test_one_bill_of_lading_proceeds(unit_settings) -> None:
    raw = raw_submission(
        make_pdf(
            [
                ["BILL OF LADING", "B/L No: KEMU123456789"],
                ["COMMERCIAL INVOICE", "B/L KEMU123456789"],
            ]
        )
    )
    try:
        validated = validate_submission(raw, unit_settings)
        assert validated.bill_of_lading_numbers == ["KEMU123456789"]
    finally:
        cleanup_raw(raw)


def _multipart(parts: list[tuple[str, str | None, bytes]], boundary: str = "bnd") -> bytes:
    body = bytearray()
    for name, filename, data in parts:
        body += f"--{boundary}\r\n".encode()
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += f"Content-Disposition: {disposition}\r\n".encode()
        if filename is not None:
            body += b"Content-Type: application/octet-stream\r\n"
        body += b"\r\n" + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body)


async def _chunks(data: bytes, size: int = 97):
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


def test_zero_byte_part_is_malformed_before_type(unit_settings) -> None:
    body = _multipart([("documents", "empty.pdf", b"")])
    with pytest.raises(errors.DomainError) as caught:
        asyncio.run(
            spool_submission(
                _chunks(body),
                "multipart/form-data; boundary=bnd",
                unit_settings,
            )
        )
    assert caught.value.code == "malformed_document"
    assert "empty" in caught.value.message


def test_whole_request_cap_rejects_unbounded_ignored_parts(
    unit_settings, temp_upload_snapshot
) -> None:
    before = temp_upload_snapshot
    limiter = unit_settings.model_copy(update={"max_request_bytes": 400})
    body = _multipart([("ignored", None, b"x" * 500)])
    with pytest.raises(errors.DomainError) as caught:
        asyncio.run(
            spool_submission(
                _chunks(body),
                "multipart/form-data; boundary=bnd",
                limiter,
            )
        )
    assert caught.value.code == "payload_too_large"
    assert caught.value.details["limit_bytes"] == 400
    assert caught.value.details["actual_bytes"] > 400
    assert temp_snapshot() <= before


def test_truncated_image_is_malformed(unit_settings) -> None:
    raw = raw_submission(truncated_image(), filenames=["truncated.jpg"])
    try:
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, unit_settings)
        assert caught.value.code == "malformed_document"
    finally:
        cleanup_raw(raw)


def test_multipage_tiff_contributes_real_frame_count(unit_settings) -> None:
    raw = raw_submission(make_multipage_tiff(3), filenames=["scan.tiff"])
    try:
        validated = validate_submission(raw, unit_settings)
        assert validated.documents[0].page_count == 3
    finally:
        cleanup_raw(raw)


def test_multipage_tiff_is_capped_by_page_limit(unit_settings) -> None:
    raw = raw_submission(make_multipage_tiff(3), filenames=["scan.tiff"])
    try:
        limiter = unit_settings.model_copy(update={"max_pages_per_file": 2})
        with pytest.raises(errors.DomainError) as caught:
            validate_submission(raw, limiter)
        assert caught.value.code == "page_limit_exceeded"
        assert caught.value.details["file_pages"] == 3
    finally:
        cleanup_raw(raw)


def test_container_probe_stops_before_text_extraction_over_cap(tmp_path) -> None:
    path = tmp_path / "three-pages.pdf"
    path.write_bytes(make_pdf([["ONE"], ["TWO"], ["THREE"]]))

    within_cap = probe_container(path, document="three-pages.pdf", page_limit=3)
    assert within_cap.page_count == 3
    assert within_cap.page_cap_breached is False
    assert len(within_cap.pages) == 3

    over_cap = probe_container(path, document="three-pages.pdf", page_limit=2)
    assert over_cap.page_count == 3
    assert over_cap.page_cap_breached is True
    assert over_cap.pages == ()


def test_overlong_multipart_boundary_is_a_caller_fault(unit_settings, temp_upload_snapshot) -> None:
    before = temp_upload_snapshot
    content_type = "multipart/form-data; boundary=" + "b" * 300
    with pytest.raises(errors.DomainError) as caught:
        asyncio.run(spool_submission(_chunks(b"anything"), content_type, unit_settings))
    assert caught.value.code == "invalid_request"
    assert caught.value.details["field"] == "Content-Type"
    assert temp_snapshot() <= before
