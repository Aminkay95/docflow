from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import IO, Any

_CHUNK = 1024 * 1024


def hash_api_key(pepper: str, token: str) -> str:
    """HMAC-SHA256(pepper, key) used for API-key storage and lookup.

    One helper keeps the bootstrap script and the request path byte-identical.
    """

    return hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def stable_json(value: Any) -> str:
    """Serialise a value deterministically: sorted keys, no insignificant spaces."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: IO[bytes]) -> tuple[str, int]:
    """Hash a binary stream and return ``(hex_digest, byte_count)``."""

    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def sha256_file(path: str | Path) -> str:
    with Path(path).open("rb") as handle:
        return sha256_stream(handle)[0]


def request_fingerprint(
    *,
    job_ref: str | None,
    webhook_url: str | None,
    file_sha256s: Iterable[str],
) -> str:
    """Fingerprint the semantic content of a submission request.

    The order of parts is significant: two calls are replays only when the same parts
    arrived in the same order.
    """

    payload = {
        "job_ref": job_ref,
        "webhook_url": webhook_url,
        "file_sha256s": list(file_sha256s),
    }
    return sha256_bytes(stable_json(payload).encode("utf-8"))


def document_idempotency_key(
    *,
    source_file_sha256: str,
    job_ref: str | None,
    page_start: int,
    page_end: int,
    document_type: str,
) -> str:
    """Per-document idempotency key.

    ``sha256(source_file_sha256 + ":" + job_ref + ":" + page_start + ":" + page_end +
    ":" + document_type)``. An ungrouped submission uses an empty job-reference segment;
    uniqueness remains scoped by the newly created ``job_id``.
    """

    raw = ":".join(
        [
            source_file_sha256,
            job_ref if job_ref is not None else "",
            str(page_start),
            str(page_end),
            str(document_type),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def merge_fingerprints(parts: Mapping[str, str]) -> str:
    """Deterministic fingerprint over named digests (helper for outbox rows)."""

    return sha256_bytes(stable_json(dict(parts)).encode("utf-8"))
