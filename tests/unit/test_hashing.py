from __future__ import annotations

import hashlib

from trade_extract.domain.hashing import (
    document_idempotency_key,
    request_fingerprint,
    sha256_bytes,
    sha256_file,
    stable_json,
)


def test_document_idempotency_key_matches_documented_formula() -> None:
    source = "a" * 64
    expected = hashlib.sha256(f"{source}:BL-1:0:2:commercial_invoice".encode()).hexdigest()
    actual = document_idempotency_key(
        source_file_sha256=source,
        job_ref="BL-1",
        page_start=0,
        page_end=2,
        document_type="commercial_invoice",
    )
    assert actual == expected


def test_document_idempotency_key_is_byte_identical_on_recompute() -> None:
    first = document_idempotency_key(
        source_file_sha256="f" * 64,
        job_ref="BL-2026-1",
        page_start=3,
        page_end=7,
        document_type="bill_of_lading",
    )
    second = document_idempotency_key(
        source_file_sha256="f" * 64,
        job_ref="BL-2026-1",
        page_start=3,
        page_end=7,
        document_type="bill_of_lading",
    )
    assert first == second
    assert len(first) == 64


def test_ungrouped_job_uses_empty_job_reference_segment() -> None:
    expected = hashlib.sha256(f"{'b' * 64}::0:0:unknown".encode()).hexdigest()
    assert (
        document_idempotency_key(
            source_file_sha256="b" * 64,
            job_ref=None,
            page_start=0,
            page_end=0,
            document_type="unknown",
        )
        == expected
    )


def test_different_job_refs_produce_different_document_keys() -> None:
    kwargs = {
        "source_file_sha256": "c" * 64,
        "page_start": 0,
        "page_end": 1,
        "document_type": "packing_list",
    }
    assert document_idempotency_key(job_ref="A", **kwargs) != document_idempotency_key(
        job_ref="B", **kwargs
    )


def test_request_fingerprint_changes_with_order_and_metadata() -> None:
    base = request_fingerprint(job_ref="BL-1", webhook_url=None, file_sha256s=["a", "b"])
    reordered = request_fingerprint(job_ref="BL-1", webhook_url=None, file_sha256s=["b", "a"])
    other_ref = request_fingerprint(job_ref="BL-2", webhook_url=None, file_sha256s=["a", "b"])
    other_url = request_fingerprint(
        job_ref="BL-1", webhook_url="https://example.test/hook", file_sha256s=["a", "b"]
    )
    assert len({base, reordered, other_ref, other_url}) == 4


def test_request_fingerprint_is_stable() -> None:
    args = {"job_ref": "BL-1", "webhook_url": None, "file_sha256s": ["a", "b"]}
    assert request_fingerprint(**args) == request_fingerprint(**args)


def test_stable_json_sorts_keys_and_omits_whitespace() -> None:
    assert stable_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'


def test_sha256_helpers(tmp_path) -> None:
    data = b"trade-extract"
    path = tmp_path / "sample.bin"
    path.write_bytes(data)
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()
