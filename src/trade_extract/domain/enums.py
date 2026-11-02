from __future__ import annotations

from enum import StrEnum


class ClientStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"


class SubmissionStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FileStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    PURGED = "purged"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class TaskKind(StrEnum):
    PREPARE_FILE = "prepare_file"
    SEGMENT_FILE = "segment_file"
    EXTRACT_SEGMENT = "extract_segment"
    FINALIZE_SUBMISSION = "finalize_submission"
    DELIVER_WEBHOOK = "deliver_webhook"
    PURGE_DOCUMENTS = "purge_documents"
    RECOVER_STALE = "recover_stale"


class WebhookEventType(StrEnum):
    RESULT_COMPLETED = "result.completed"
    RESULT_REVIEW_REQUIRED = "result.review_required"
    RESULT_FAILED = "result.failed"


class WebhookStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class DocumentType(StrEnum):
    COMMERCIAL_INVOICE = "commercial_invoice"
    PROFORMA_INVOICE = "proforma_invoice"
    BILL_OF_LADING = "bill_of_lading"
    DELIVERY_ORDER = "delivery_order"
    PORT_PASS = "port_pass"
    CUSTOM_ENTRY = "custom_entry"
    VERIFICATION_MEMO = "verification_memo"
    PACKING_LIST = "packing_list"
    UNKNOWN = "unknown"


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    REVIEW_REQUIRED = "review_required"
    RESOLVED = "resolved"
    UNREADABLE = "unreadable"
    FAILED = "failed"
