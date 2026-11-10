from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from python_multipart.multipart import MultipartParser, parse_options_header

from trade_extract.config import Settings
from trade_extract.domain import errors
from trade_extract.services.page_model import (
    ContainerProbe,
    PageProbe,
    probe_container,
    probe_image,
)

_PRINTABLE_ASCII = re.compile(r"^[\x20-\x7e]+$")
_BL_LABEL = re.compile(
    r"(?:\bB\s*/\s*L\b|\bOCEAN\s+BILL\s+OF\s+LADING\b|\bBILL\s+OF\s+LADING\b|\bWAYBILL\b)",
    re.IGNORECASE,
)
#: Optional words/punctuation that sit between a B/L label and its number.
_BL_SEPARATOR = re.compile(r"^(?:NO\b\.?|NUMBER\b|#|:)\s*", re.IGNORECASE)
#: The first token after the label; scanning stops at whitespace, slash or comma.
_BL_TOKEN = re.compile(r"^([^\s/,]+)")
_SNIFF_BYTES = 16
_FIELD_MEMORY_CAP = 8192
_CHUNK = 64 * 1024

_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "tiff": "image/tiff",
}
_EXTENSIONS = {
    "pdf": "pdf",
    "jpeg": "jpg",
    "png": "png",
    "webp": "webp",
    "tiff": "tif",
}


@dataclass(frozen=True)
class SpooledDocument:
    """A temp-spooled file that has passed size and magic-byte checks."""

    filename: str
    declared_content_type: str | None
    temp_path: Path
    size_bytes: int
    sha256: str
    detected_type: str

    @property
    def content_type(self) -> str:
        return _MEDIA_TYPES[self.detected_type]

    @property
    def extension(self) -> str:
        return _EXTENSIONS[self.detected_type]


@dataclass
class RawSubmission:
    job_ref: str | None
    webhook_url: str | None
    documents: list[SpooledDocument]
    temp_dir: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


@dataclass(frozen=True)
class ValidatedDocument:
    spooled: SpooledDocument
    probe: ContainerProbe

    @property
    def page_count(self) -> int:
        return self.probe.page_count

    @property
    def pages(self) -> tuple[PageProbe, ...]:
        return self.probe.pages


@dataclass
class ValidatedSubmission:
    raw: RawSubmission
    documents: list[ValidatedDocument]
    total_pages: int
    bill_of_lading_numbers: list[str] = field(default_factory=list)

    @property
    def job_ref(self) -> str | None:
        return self.raw.job_ref

    @property
    def webhook_url(self) -> str | None:
        return self.raw.webhook_url

    def cleanup(self) -> None:
        self.raw.cleanup()


class _MultipartCollector:
    """Callback-driven multipart reader that enforces limits while streaming."""

    def __init__(self, settings: Settings, temp_dir: Path) -> None:
        self._settings = settings
        self._temp_dir = temp_dir
        self._headers: dict[bytes, bytes] = {}
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._file_handle: Any = None
        self._digest: Any = None
        self._size = 0
        self._sniff = bytearray()
        self._detected: str | None = None
        self._filename = ""
        self._declared_content_type: str | None = None
        self._current_name: bytes | None = None
        self._current_path: Path | None = None
        self._field_buffer: bytearray | None = None
        self._field_name: str | None = None
        self._ignore_part = False

        self.documents: list[SpooledDocument] = []
        self.fields: dict[str, str] = {}

    # -- callbacks ---------------------------------------------------------
    def on_part_begin(self) -> None:
        self._headers = {}
        self._header_field = bytearray()
        self._header_value = bytearray()
        self._current_name = None
        self._ignore_part = False
        self._field_buffer = None
        self._field_name = None
        self._file_handle = None
        self._digest = None
        self._size = 0
        self._sniff = bytearray()
        self._detected = None
        self._filename = ""
        self._declared_content_type = None
        self._current_path = None

    def on_part_abort(self) -> None:
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            finally:
                self._file_handle = None
        self._field_buffer = None

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        key = bytes(self._header_field).strip().lower()
        value = bytes(self._header_value).strip()
        if key:
            self._headers[key] = value
        self._header_field = bytearray()
        self._header_value = bytearray()

    def on_headers_finished(self) -> None:
        disposition = self._headers.get(b"content-disposition", b"")
        _, params = parse_options_header(disposition)
        self._current_name = params.get(b"name")
        if self._current_name == b"documents":
            count = len(self.documents) + 1
            if count > self._settings.max_documents_per_group:
                raise errors.too_many_documents(self._settings.max_documents_per_group, count)
            raw_filename = params.get(b"filename") or b""
            filename = Path(raw_filename.decode("utf-8", "replace")).name or (f"document-{count}")
            self._filename = filename[:255]
            declared = self._headers.get(b"content-type")
            self._declared_content_type = declared.decode("latin-1") if declared else None
            path = self._temp_dir / f"part-{count:03d}.upload"
            self._file_handle = path.open("wb")
            self._digest = hashlib.sha256()
            self._current_path = path
            self._size = 0
            self._sniff = bytearray()
            self._detected = None
        elif self._current_name in (b"job_ref", b"webhook_url"):
            self._field_name = self._current_name.decode("ascii")
            self._field_buffer = bytearray()
        else:
            self._ignore_part = True

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self._file_handle is not None:
            self._size += len(chunk)
            if self._size > self._settings.max_file_bytes:
                self._file_handle.close()
                raise errors.file_too_large(
                    self._filename,
                    self._settings.max_file_bytes,
                    self._size,
                )
            if self._detected is None and len(self._sniff) < _SNIFF_BYTES:
                remaining = _SNIFF_BYTES - len(self._sniff)
                self._sniff.extend(chunk[:remaining])
                detected = detect_media_type(bytes(self._sniff))
                if detected is not None:
                    self._detected = detected
                elif len(self._sniff) >= _SNIFF_BYTES:
                    raise errors.unsupported_media_type(
                        self._filename, self._settings.allowed_content_types
                    )
            self._file_handle.write(chunk)
            self._digest.update(chunk)
        elif self._field_buffer is not None:
            if len(self._field_buffer) + len(chunk) > _FIELD_MEMORY_CAP:
                raise errors.invalid_request(self._field_name or "field", "value is too long")
            self._field_buffer.extend(chunk)

    def on_part_end(self) -> None:
        if self._file_handle is not None:
            self._file_handle.close()
            self._file_handle = None
            # A zero-byte part is malformed before it is a type problem.
            if self._size == 0:
                raise errors.malformed_document(self._filename, "the file is empty")
            # Very short parts may end before the sniff window filled.
            if self._detected is None:
                self._detected = detect_media_type(bytes(self._sniff))
            if self._detected is None:
                raise errors.unsupported_media_type(
                    self._filename, self._settings.allowed_content_types
                )
            assert self._current_path is not None
            self.documents.append(
                SpooledDocument(
                    filename=self._filename,
                    declared_content_type=self._declared_content_type,
                    temp_path=self._current_path,
                    size_bytes=self._size,
                    sha256=self._digest.hexdigest(),
                    detected_type=self._detected,
                )
            )
            self._current_path = None
        elif self._field_buffer is not None and self._field_name is not None:
            try:
                value = bytes(self._field_buffer).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise errors.invalid_request(self._field_name, "value must be UTF-8 text") from exc
            self.fields[self._field_name] = value

    # -- parser wiring -----------------------------------------------------
    def callbacks(self) -> Any:
        return {
            "on_part_begin": self.on_part_begin,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
        }


def detect_media_type(header: bytes) -> str | None:
    """Detect a supported container from magic bytes only.

    A declared Content-Type or filename is never consulted.
    """

    if len(header) >= 5 and header[:5] == b"%PDF-":
        return "pdf"
    if len(header) >= 3 and header[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(header) >= 8 and header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    if len(header) >= 4 and header[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def validate_job_ref(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    if not value:
        raise errors.invalid_request("job_ref", "must not be empty")
    if len(value) > settings.max_job_ref_length:
        raise errors.invalid_request(
            "job_ref",
            f"must be at most {settings.max_job_ref_length} printable ASCII characters",
        )
    if not _PRINTABLE_ASCII.fullmatch(value):
        raise errors.invalid_request("job_ref", "must contain printable ASCII characters only")
    return value


def validate_idempotency_key(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    if len(value) < settings.min_idempotency_key_length:
        raise errors.invalid_request(
            "Idempotency-Key",
            f"must be at least {settings.min_idempotency_key_length} printable characters",
        )
    if len(value) > settings.max_idempotency_key_length:
        raise errors.invalid_request(
            "Idempotency-Key",
            f"must be at most {settings.max_idempotency_key_length} printable characters",
        )
    if not _PRINTABLE_ASCII.fullmatch(value):
        raise errors.invalid_request("Idempotency-Key", "must contain printable characters only")
    return value


def validate_webhook_url(value: str | None, settings: Settings) -> str | None:
    if value is None or value.strip() == "":
        # An empty optional field means "absent", not "invalid".
        return None
    remedy: dict[str, Any] = {
        "max_length": settings.max_webhook_url_length,
        "require_https": settings.require_https_webhooks,
    }
    if len(value) > settings.max_webhook_url_length:
        raise errors.invalid_webhook_url(
            f"must be at most {settings.max_webhook_url_length} characters",
            value=value,
            **remedy,
        )
    if any(character.isspace() for character in value):
        raise errors.invalid_webhook_url("must not contain whitespace", value=value, **remedy)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise errors.invalid_webhook_url("must be an absolute URL", value=value, **remedy) from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise errors.invalid_webhook_url("must be an absolute http(s) URL", value=value, **remedy)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise errors.invalid_webhook_url(
            "must not embed credentials in the URL", value=value, **remedy
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise errors.invalid_webhook_url(
            "must use a numeric port between 1 and 65535", value=value, **remedy
        ) from exc
    if port is not None and not 1 <= port <= 65535:
        raise errors.invalid_webhook_url(
            "must use a numeric port between 1 and 65535", value=value, **remedy
        )
    if settings.require_https_webhooks and parsed.scheme != "https":
        raise errors.invalid_webhook_url(
            "must use HTTPS outside development", value=value, **remedy
        )
    return value


def normalize_bill_of_lading(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", token).upper()


def extract_bill_of_lading_numbers(page_texts: Iterable[str]) -> list[str]:
    """Return distinct B/L numbers anchored to an explicit label on the same line.

    Positive evidence only: the candidate is the *single token immediately following*
    a ``B/L``, ``BILL OF LADING``, ``OCEAN BILL OF LADING`` or ``WAYBILL`` label,
    optionally separated by ``NO``, ``NUMBER``, ``#`` or ``:``. Scanning stops at the
    first whitespace, slash or comma after that token, so dates, seals, invoice numbers
    and container numbers elsewhere on the line are never evidence. A missing text
    layer, a missing label or an unparseable token is ambiguity, and ambiguity proceeds.
    """

    found: set[str] = set()
    for text in page_texts:
        for line in text.splitlines():
            for label in _BL_LABEL.finditer(line):
                remainder = line[label.end() :].lstrip()
                while True:
                    separator = _BL_SEPARATOR.match(remainder)
                    if separator is None:
                        break
                    remainder = remainder[separator.end() :].lstrip()
                token_match = _BL_TOKEN.match(remainder)
                if token_match is None:
                    continue
                normalized = normalize_bill_of_lading(token_match.group(1))
                if not normalized:
                    continue
                if not any(character.isalpha() for character in normalized):
                    continue
                if sum(character.isdigit() for character in normalized) < 3:
                    continue
                found.add(normalized)
    return sorted(found)


def validate_submission(raw: RawSubmission, settings: Settings) -> ValidatedSubmission:
    """Apply page limits and the multi-shipment guard to already-spooled files.

    Pure with respect to HTTP: callers can unit-test it with temp files. It never
    stores anything, and it never mutates the caller's group.
    """

    if not raw.documents:
        raise errors.no_documents(settings.max_documents_per_group)
    if len(raw.documents) > settings.max_documents_per_group:
        raise errors.too_many_documents(settings.max_documents_per_group, len(raw.documents))

    validated: list[ValidatedDocument] = []
    submission_pages = 0
    all_page_texts: list[str] = []

    for spooled in raw.documents:
        remaining_submission_pages = settings.max_pages_per_submission - submission_pages
        page_limit = min(settings.max_pages_per_file, max(remaining_submission_pages, 1))
        if spooled.detected_type == "pdf":
            probe = probe_container(
                spooled.temp_path,
                document=spooled.filename,
                page_limit=page_limit,
            )
        else:
            probe = probe_image(
                spooled.temp_path,
                document=spooled.filename,
                page_limit=page_limit,
            )

        submission_pages += probe.page_count
        if (
            probe.page_cap_breached
            or probe.page_count > settings.max_pages_per_file
            or submission_pages > settings.max_pages_per_submission
        ):
            raise errors.page_limit_exceeded(
                spooled.filename,
                file_pages=probe.page_count,
                submission_pages=submission_pages,
                max_pages_per_file=settings.max_pages_per_file,
                max_pages_per_submission=settings.max_pages_per_submission,
            )

        all_page_texts.extend(page.text for page in probe.pages)
        validated.append(ValidatedDocument(spooled=spooled, probe=probe))

    bl_numbers = extract_bill_of_lading_numbers(all_page_texts)
    if len(bl_numbers) >= 2:
        raise errors.multi_shipment_container(bl_numbers)

    return ValidatedSubmission(
        raw=raw,
        documents=validated,
        total_pages=submission_pages,
        bill_of_lading_numbers=bl_numbers,
    )


async def spool_submission(
    chunks: AsyncIterator[bytes],
    content_type: str | None,
    settings: Settings,
) -> RawSubmission:
    """Stream a multipart body into a private temp directory.

    Group count, per-file size and magic-byte checks run as bytes arrive, before the
    next part is read. On any failure the temp directory is removed.
    """

    if not content_type:
        raise errors.invalid_request("Content-Type", "must be multipart/form-data with a boundary")
    media_type, params = parse_options_header(content_type)
    boundary = params.get(b"boundary") or b""
    if media_type != b"multipart/form-data" or not boundary:
        raise errors.invalid_request("Content-Type", "must be multipart/form-data with a boundary")
    if len(boundary) > 256:
        raise errors.invalid_request(
            "Content-Type", "the multipart boundary must be at most 256 characters"
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="trade-extract-upload-"))
    collector: _MultipartCollector | None = None
    total_bytes = 0
    try:
        collector = _MultipartCollector(settings, temp_dir)
        parser = MultipartParser(boundary, collector.callbacks())
        async for chunk in chunks:
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > settings.max_request_bytes:
                raise errors.payload_too_large(settings.max_request_bytes, total_bytes)
            # The parser callbacks are synchronous; run them off the event loop while
            # awaiting each write so one large upload cannot stall other requests.
            await asyncio.to_thread(parser.write, chunk)
        await asyncio.to_thread(parser.finalize)
    except errors.DomainError:
        if collector is not None:
            collector.on_part_abort()
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except (OSError, MemoryError) as exc:
        # A disk or memory failure on our side is not the caller's fault: report it
        # as retryable so the client does not treat a healthy file as rejected.
        if collector is not None:
            collector.on_part_abort()
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise errors.service_unavailable("temporary storage failure") from exc
    except Exception as exc:  # noqa: BLE001 - multipart errors are caller faults
        if collector is not None:
            collector.on_part_abort()
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise errors.invalid_request("documents", "the multipart body could not be parsed") from exc

    assert collector is not None
    raw = RawSubmission(
        job_ref=collector.fields.get("job_ref") or None,
        webhook_url=collector.fields.get("webhook_url"),
        documents=collector.documents,
        temp_dir=temp_dir,
    )
    try:
        if not raw.documents:
            raise errors.no_documents(settings.max_documents_per_group)
        raw.job_ref = validate_job_ref(raw.job_ref, settings)
        raw.webhook_url = validate_webhook_url(raw.webhook_url, settings)
    except errors.DomainError:
        raw.cleanup()
        raise
    return raw
