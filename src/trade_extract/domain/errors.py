from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

DOCS_BASE_URL = "https://docs.trade-extract.example/errors"


class DomainError(Exception):
    """A typed failure that maps to exactly one HTTP response shape.

    ``message`` always names the rule that was broken and the remediation available to
    the caller. ``details`` carries machine-readable evidence only.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
        doc_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details: dict[str, Any] = details or {}
        self.doc_url = doc_url or f"{DOCS_BASE_URL}#{code}"


def invalid_request(field: str, rule: str, *, value: Any = None) -> DomainError:
    details: dict[str, Any] = {"field": field, "rule": rule}
    if value is not None:
        details["value"] = value
    return DomainError(
        "invalid_request",
        f"'{field}' is invalid: {rule}. Correct the field and resubmit.",
        http_status=400,
        details=details,
    )


def unauthorized(
    message: str = "A valid API key is required. Send 'Authorization: Bearer <key>'.",
) -> DomainError:
    return DomainError("unauthorized", message, http_status=401)


def forbidden(scope: str) -> DomainError:
    return DomainError(
        "forbidden",
        f"This API key lacks the '{scope}' scope. Use a key that grants '{scope}'.",
        http_status=403,
        details={"required_scope": scope},
    )


def client_inactive(status: str) -> DomainError:
    return DomainError(
        "client_inactive",
        f"This account is '{status}' and cannot submit or read results. "
        "Contact the account owner to reactivate it, then retry with an active account.",
        http_status=403,
        details={"account_status": status},
    )


def result_not_found() -> DomainError:
    return DomainError(
        "result_not_found",
        "No result exists for this id for this account. Check the result_id and the API key.",
        http_status=404,
    )


def job_not_found() -> DomainError:
    return DomainError(
        "job_not_found",
        "No job exists for this id for this account. Check the job_id and the API key.",
        http_status=404,
    )


def document_not_found() -> DomainError:
    return DomainError(
        "document_not_found",
        "No document exists for this id for this account. Check the document_id and the API key.",
        http_status=404,
    )


def idempotency_key_reused() -> DomainError:
    return DomainError(
        "idempotency_key_reused",
        "This Idempotency-Key was already used with a different request body. "
        "Use a new Idempotency-Key for a different submission.",
        http_status=409,
    )


def job_failed() -> DomainError:
    return DomainError(
        "job_failed",
        "This job_ref names a failed job, which cannot accept more documents. "
        "Use a new job_ref for this shipment.",
        http_status=409,
    )


def too_many_documents(limit: int, actual: int) -> DomainError:
    return DomainError(
        "too_many_documents",
        f"This group contains {actual} files; the limit is {limit}. "
        f"Split it into groups of {limit} or fewer and submit again.",
        details={"limit": limit, "actual": actual},
    )


def no_documents(limit: int) -> DomainError:
    return DomainError(
        "no_documents",
        f"A submission group needs at least 1 and at most {limit} files. "
        "Attach one or more 'documents' file parts.",
        details={"limit_min": 1, "limit_max": limit},
    )


def file_too_large(document: str, limit_bytes: int, actual_bytes: int) -> DomainError:
    limit_mb = limit_bytes / (1024 * 1024)
    limit_text = f"{limit_mb:.0f} MB" if limit_mb >= 1 else f"{limit_bytes} bytes"
    return DomainError(
        "file_too_large",
        f"Document '{document}' is {actual_bytes} bytes; the limit is {limit_bytes} bytes. "
        f"Compress or downscale the file below {limit_text} and resubmit.",
        http_status=413,
        details={
            "document": document,
            "limit_bytes": limit_bytes,
            "actual_bytes": actual_bytes,
        },
    )


def payload_too_large(limit_bytes: int, actual_bytes: int) -> DomainError:
    return DomainError(
        "payload_too_large",
        f"This request body is {actual_bytes} bytes; the limit is {limit_bytes} bytes. "
        "Split the group into smaller submissions and resubmit.",
        http_status=413,
        details={"limit_bytes": limit_bytes, "actual_bytes": actual_bytes},
    )


def unsupported_media_type(document: str, accepted: list[str]) -> DomainError:
    accepted_text = ", ".join(accepted)
    return DomainError(
        "unsupported_media_type",
        f"Document '{document}' is not an accepted type. Use one of: {accepted_text}.",
        http_status=415,
        details={"document": document, "accepted_types": accepted},
    )


def encrypted_document(document: str) -> DomainError:
    return DomainError(
        "encrypted_document",
        f"Document '{document}' is password-protected or encrypted. "
        "Remove the password and resubmit.",
        details={"document": document},
    )


def malformed_document(document: str, reason: str = "the container cannot be read") -> DomainError:
    return DomainError(
        "malformed_document",
        f"Document '{document}' is malformed ({reason}) or has zero pages. "
        "Re-export the file as a fresh PDF or image and resubmit.",
        details={"document": document, "reason": reason},
    )


def processing_failed(
    document: str, reason: str = "the source could not be read or rendered"
) -> DomainError:
    """A worker-side preparation failure for one uploaded file.

    The task retry/dead path owns the outcome; this error exists so the recorded
    reason names the document instead of an opaque library exception.
    """

    return DomainError(
        "processing_failed",
        f"Document '{document}' could not be prepared ({reason}). "
        "Re-upload a readable copy and resubmit.",
        http_status=500,
        details={"document": document, "reason": reason},
    )


def page_limit_exceeded(
    document: str,
    *,
    file_pages: int,
    submission_pages: int,
    max_pages_per_file: int,
    max_pages_per_submission: int,
) -> DomainError:
    return DomainError(
        "page_limit_exceeded",
        (
            f"Document '{document}' brings the submission to {submission_pages} pages; "
            f"the limits are {max_pages_per_file} pages per file and "
            f"{max_pages_per_submission} pages per submission. "
            "Split the file or the group and resubmit."
        ),
        details={
            "file_pages": file_pages,
            "submission_pages": submission_pages,
            "limits": {
                "max_pages_per_file": max_pages_per_file,
                "max_pages_per_submission": max_pages_per_submission,
            },
        },
    )


def multi_shipment_container(numbers: list[str]) -> DomainError:
    listed = ", ".join(numbers)
    return DomainError(
        "multi_shipment_container",
        f"This group contains {len(numbers)} shipments (B/L {listed}). "
        "Split it and resubmit one shipment per job_ref.",
        details={
            "shipment_count": len(numbers),
            "bill_of_lading_numbers": numbers,
        },
    )


def invalid_webhook_url(
    reason: str,
    *,
    value: str | None = None,
    max_length: int | None = None,
    require_https: bool = False,
) -> DomainError:
    details: dict[str, Any] = {"field": "webhook_url", "rule": reason}
    if value is not None:
        details["redacted"] = redact_url(value)
    return DomainError(
        "invalid_request",
        f"'webhook_url' is invalid: {reason}. {_webhook_remedy(reason, max_length, require_https)}",
        http_status=400,
        details=details,
    )


def _webhook_remedy(reason: str, max_length: int | None, require_https: bool) -> str:
    lowered = reason.lower()
    if "credentials" in lowered:
        return "Remove the userinfo (user:password@host) from the URL and resubmit."
    if "whitespace" in lowered:
        return "Percent-encode whitespace or use a URL without spaces."
    if "at most" in lowered:
        return "Shorten the URL and resubmit."
    if "port" in lowered:
        return "Use a URL with a numeric port between 1 and 65535, or omit the port."
    if "resolved" in lowered:
        return "Use a hostname that resolves publicly, then resubmit."
    if "private" in lowered or "loopback" in lowered or "metadata" in lowered:
        return "Use a public endpoint and resubmit."
    scheme = "HTTPS" if (require_https or "https" in lowered) else "absolute http(s)"
    limit = f" of at most {max_length} characters" if max_length is not None else ""
    return f"Provide an {scheme} URL{limit}."


def redact_url(value: str) -> str:
    """Reduce a rejected URL to its scheme and host before it reaches the caller."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[invalid url]"
    host = parsed.hostname or ""
    if not host:
        return "[invalid url]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def internal_error() -> DomainError:
    return DomainError(
        "internal_error",
        "An unexpected error occurred. Retry the request and quote the request id if it persists.",
        http_status=500,
    )


def service_unavailable(reason: str) -> DomainError:
    return DomainError(
        "service_unavailable",
        f"The service is temporarily unavailable ({reason}). "
        "Retry shortly; the request id is your support handle.",
        http_status=503,
        details={"reason": reason},
    )
