# 04 — API Contract

Base URL: `https://api.trade-extract.example/v1` (the host is deployment-specific).

All examples are illustrative. The machine-readable contract is [`openapi.yaml`](openapi.yaml).

## 1. Conventions

| Concern | Rule |
|---|---|
| Transport | HTTPS only in production. |
| Format | JSON for responses; `multipart/form-data` for uploads. |
| Auth | `Authorization: Bearer tx_live_…` on every public endpoint except health. |
| IDs | Opaque, prefixed strings (`job_…`, `res_…`, `doc_…`). Never parse them. |
| Timestamps | ISO 8601 with `Z`, UTC. |
| Money and quantities | Returned as stated strings plus `value_type`; clients parse with their own locale rules. |
| Request ID | `X-Request-Id` accepted; always returned. Generated if absent. |
| Errors | One envelope shape, one stable `error.code`. |
| Pagination | Cursor-based, `limit` + `cursor` → `next_cursor`. |
| Idempotency | `Idempotency-Key` header for submissions; deterministic per-document keys internally. |
| Versioning | Major version in the path. Additive changes only within `v1`. |
| Content negotiation | `Accept: application/json`; `406` if unsupported. |

### Authentication

API keys are created out of band and shown once. The key format is:

```text
tx_live_<32 bytes base64url>       production
tx_test_<32 bytes base64url>       non-production
```

The server stores only `HMAC-SHA256(pepper, key)` and a short prefix for lookup. A revoked or
expired key returns `401 unauthorized`. A valid key without the required scope returns
`403 forbidden`.

Scopes:

| Scope | Grants |
|---|---|
| `extract:write` | `POST /v1/extract` |
| `results:read` | Result, job, document and conflict reads |
| `account:read` | Usage, policy and export reads |
| `account:write` | Export creation and deletion requests |

The bootstrap development key is for local use only. Production keys are created by an operator or
the admin console and are never placed in the repository.

## 2. Endpoints

| Method | Path | Purpose | Scope |
|---|---|---|---|
| `POST` | `/v1/extract` | Submit 1–20 documents under an optional `job_ref`. | `extract:write` |
| `GET` | `/v1/results/{result_id}` | Poll the result of one submission. | `results:read` |
| `GET` | `/v1/jobs/{job_id}` | Read the logical job, all documents and conflicts. | `results:read` |
| `GET` | `/v1/jobs/{job_id}/documents` | Paginated documents in a job. | `results:read` |
| `GET` | `/v1/jobs/{job_id}/conflicts` | Paginated conflicts in a job. | `results:read` |
| `GET` | `/v1/documents/{document_id}` | Read one document with fields and provenance. | `results:read` |
| `GET` | `/v1/policy` | Read the active policy version and review configuration. | `account:read` |
| `GET` | `/v1/usage` | Read current-period usage and limits. | `account:read` |
| `POST` | `/v1/account/exports` | Start a full extraction-record export. | `account:write` |
| `GET` | `/v1/account/exports/{export_id}` | Poll an export and obtain a download URL. | `account:read` |
| `POST` | `/v1/account/deletion-requests` | Request deletion of extraction records. | `account:write` |
| `GET` | `/v1/health` | Liveness. No auth. | — |
| `GET` | `/v1/health/ready` | Readiness: DB, storage, active policy. No auth. | — |

There is no public endpoint for the review console. Reviewers use the session-authenticated
`/review` UI described in [`05-ui-architecture.md`](05-ui-architecture.md). Internal admin JSON is
under `/internal/v1` and is protected by operator credentials, never by a customer API key.

## 3. Submit documents

### `POST /v1/extract`

`Content-Type: multipart/form-data`

| Part | Required | Rules |
|---|---|---|
| `documents` | Yes, 1–20 | Repeated file parts. Each ≤ 25 MiB. PDF, JPEG, PNG, WebP or TIFF. |
| `job_ref` | No | Opaque caller reference, 1–128 printable ASCII characters. Same value accumulates into one job. |
| `webhook_url` | No | Per-submission override. HTTPS in production, ≤ 2048 chars. Falls back to the client default. |

Headers:

| Header | Required | Meaning |
|---|---|---|
| `Authorization` | Yes | Bearer API key. |
| `Idempotency-Key` | No | 8–128 printable characters. Same key + same request returns the same result. |
| `X-Request-Id` | No | Client correlation ID; echoed. |

A file part may include a filename, but the filename is metadata only. Document type is determined
by segmentation, never by the filename or a client hint.

#### Validation order

The API enforces limits before any object is stored:

1. Authentication and scope.
2. Group size: at least one document, at most 20.
3. Per-file size: at most 26,214,400 bytes (25 MiB).
4. Magic-byte type detection; declared `Content-Type` is ignored for trust.
5. Page count after parsing: at most 500 per file, 2,000 per submission.
6. Encryption, malformed structure, zero pages, macro or archive content.
7. Multi-shipment guard: positive evidence of two or more distinct bill-of-lading numbers in the
   submission returns `422 multi_shipment_container`, naming the count and instructing the caller to
   split. Ambiguous evidence proceeds to review.

A rejected request creates no job, no submission and no stored object.

#### Response `202 Accepted`

```http
HTTP/1.1 202 Accepted
Location: /v1/results/res_01J9Z3K4T7Q8M2
X-Request-Id: req_01J9Z3K4T7Q8M2
Retry-After: 2
Content-Type: application/json
```

```json
{
  "result_id": "res_01J9Z3K4T7Q8M2",
  "job_id": "job_01J9Z3K4T7Q8M2",
  "job_ref": "BL-2026-00417",
  "status": "queued",
  "submitted_documents": 3,
  "duplicate_documents": 0,
  "status_url": "https://api.trade-extract.example/v1/results/res_01J9Z3K4T7Q8M2",
  "job_url": "https://api.trade-extract.example/v1/jobs/job_01J9Z3K4T7Q8M2",
  "created_at": "2026-10-31T09:12:44Z"
}
```

| Field | Meaning |
|---|---|
| `result_id` | The polling handle for this submission. |
| `job_id` | The logical accumulator. Reused when `job_ref` already exists for the client. |
| `job_ref` | Echo of the caller reference, or `null`. |
| `status` | `queued`, `processing`, `completed`, `failed`. |
| `submitted_documents` | Number of file parts accepted in this call. |
| `duplicate_documents` | Number of files already present in the job and therefore not reprocessed. |
| `status_url` | Relative or absolute URL to poll. |

`Location` points at the result resource. `Retry-After` is a hint, not a contract.

#### Idempotency

Two layers:

1. **Submission idempotency.** If `Idempotency-Key` is supplied, the same key and an identical
   request fingerprint returns the original `202` body. The same key with a different body returns
   `409 idempotency_key_reused`.
2. **Per-document idempotency.** Every document gets:

   ```text
   idempotency_key = sha256(source_file_sha256 + ":" + job_ref + ":" + page_start + ":" + page_end + ":" + document_type)
   ```

   `(job_id, idempotency_key)` is unique. Re-submitting the same file into the same job does not
   create a second document; the existing output is returned and counted in
   `duplicate_documents`.

If all submitted files are duplicates, the response is still `202`; the result is immediately
`completed` with the existing documents.

#### Accumulation under `job_ref`

The same `job_ref` may be called repeatedly. Each call creates a new submission and result, but all
documents attach to one job. Conflicts are recomputed across the whole job after every submission.

A completed job reopens when a new submission arrives: its status returns to `queued`,
`terminal_at` is cleared and the file-retention clock restarts when it next reaches terminal. The
retained extraction records are enough to reconcile against documents whose source files were
already purged. A failed job rejects further submissions with `409 job_failed`; use a new
`job_ref`.

The platform never infers that two documents belong together. If `job_ref` is omitted, every
submission gets a new job of its own; that job cannot accumulate later submissions.

#### Example

```bash
curl -X POST https://api.trade-extract.example/v1/extract \
  -H "Authorization: Bearer $TRADE_EXTRACT_API_KEY" \
  -H "Idempotency-Key: 7f4c2b1e-2026-10-31" \
  -F 'job_ref=BL-2026-00417' \
  -F 'documents=@invoice.pdf;type=application/pdf' \
  -F 'documents=@packing-list.pdf;type=application/pdf' \
  -F 'documents=@bill-of-lading.pdf;type=application/pdf'
```

## 4. Poll a result

### `GET /v1/results/{result_id}`

Query parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `include` | `documents,conflicts,decision` | Comma list: `documents`, `conflicts`, `checks`, `provenance`, `review`, `decision`. |
| `document_id` | — | Restrict `documents` to one id. |

The result contains exactly one output object per document row produced from **this** submission's
files — that is, one per segment. A duplicate part produces no document row and is reported only
through `duplicate_documents`; an earlier submission's documents are never re-listed. Job-wide
conflicts involving any of those documents are included.

#### Processing response

```json
{
  "result_id": "res_01J9Z3K4T7Q8M2",
  "job_id": "job_01J9Z3K4T7Q8M2",
  "job_ref": "BL-2026-00417",
  "status": "processing",
  "progress": {
    "documents_total": 3,
    "documents_completed": 1,
    "documents_failed": 0,
    "pages_total": 14,
    "pages_read": 6
  },
  "created_at": "2026-10-31T09:12:44Z",
  "completed_at": null,
  "documents": [
    {
      "document_id": "doc_01J9Z4A1",
      "status": "processing",
      "document_type": null,
      "fields": null
    }
  ],
  "conflicts": [],
  "decision": null,
  "policy": { "version": 3 }
}
```

`Cache-Control: no-store`. An `ETag` is provided; `If-None-Match` returns `304` when the resource
has not changed. When `status` is `queued` or `processing`, the response includes
`Retry-After: 2`.

#### Completed response

```json
{
  "result_id": "res_01J9Z3K4T7Q8M2",
  "job_id": "job_01J9Z3K4T7Q8M2",
  "job_ref": "BL-2026-00417",
  "status": "completed",
  "progress": {
    "documents_total": 3,
    "documents_completed": 3,
    "documents_failed": 0,
    "pages_total": 14,
    "pages_read": 14
  },
  "created_at": "2026-10-31T09:12:44Z",
  "completed_at": "2026-10-31T09:15:02Z",
  "documents": [
    {
      "document_id": "doc_01J9Z4A1",
      "result_id": "res_01J9Z3K4T7Q8M2",
      "job_id": "job_01J9Z3K4T7Q8M2",
      "job_ref": "BL-2026-00417",
      "file_id": "file_01J9Z3K5",
      "segment_id": "seg_01J9Z4A0",
      "document_type": "commercial_invoice",
      "document_type_confidence": 0.99,
      "schema_version": "invoice.peppol-1.0",
      "status": "review_required",
      "source": {
        "filename": "invoice.pdf",
        "content_type": "application/pdf",
        "file_hash": "sha256:0f3c…",
        "document_hash": "sha256:91ab…",
        "page_start": 0,
        "page_end": 1
      },
      "fields": {
        "invoice.number": {
          "kind": "scalar",
          "value": "INV-2026-0417",
          "value_type": "string",
          "raw_text": "INVOICE NO: INV-2026-0417",
          "provenance": {
            "page_index": 0,
            "page_number": 1,
            "bbox": { "x0": 0.61, "y0": 0.08, "x1": 0.82, "y1": 0.11 },
            "coordinate_space": "normalized_top_left",
            "page_width_pt": 595.0,
            "page_height_pt": 842.0,
            "reader": "text_layer",
            "reader_version": "pypdfium2-4.30.0",
            "model": null,
            "confidence": 1.0
          }
        },
        "shipment.gross_weight_kg": {
          "kind": "scalar",
          "value": "18,450.00",
          "value_type": "decimal",
          "raw_text": "GROSS WEIGHT 18,450.00 KGS",
          "provenance": {
            "page_index": 1,
            "page_number": 2,
            "bbox": { "x0": 0.62, "y0": 0.44, "x1": 0.78, "y1": 0.46 },
            "coordinate_space": "normalized_top_left",
            "page_width_pt": 595.0,
            "page_height_pt": 842.0,
            "reader": "vision",
            "reader_version": "deepseek-flash",
            "model": "deepseek-flash",
            "confidence": 0.94
          },
          "agreement": "unanimous"
        }
      },
      "extension": {},
      "checks": [
        {
          "code": "INV-003",
          "tier": 1,
          "status": "fail",
          "field_path": "totals.payable_amount",
          "expected": "18450.00",
          "actual": "18540.00",
          "severity": "blocking",
          "message": "The sum of line extension amounts is 18,450.00, but the stated payable amount is 18,540.00."
        }
      ],
      "conflicts": [
        {
          "conflict_id": "cnf_01J9Z5B2",
          "canonical_key": "shipment.gross_weight_kg",
          "field_label": "Gross weight",
          "status": "open",
          "sources": [
            {
              "document_id": "doc_01J9Z4A1",
              "document_type": "commercial_invoice",
              "value": "18,450.00",
              "provenance": { "page_index": 1, "bbox": { "x0": 0.62, "y0": 0.44, "x1": 0.78, "y1": 0.46 } }
            },
            {
              "document_id": "doc_01J9Z4B7",
              "document_type": "packing_list",
              "value": "18,540.00",
              "provenance": { "page_index": 3, "bbox": { "x0": 0.20, "y0": 0.71, "x1": 0.37, "y1": 0.73 } }
            }
          ]
        }
      ],
      "decision": {
        "value": "review",
        "reasons": [
          "check_failed:INV-003",
          "conflict_open:shipment.gross_weight_kg"
        ]
      },
      "review": {
        "extraction_open": true,
        "conflict_open": true,
        "url": null
      },
      "created_at": "2026-10-31T09:12:44Z",
      "extracted_at": "2026-10-31T09:14:50Z"
    }
  ],
  "conflicts": [
    {
      "conflict_id": "cnf_01J9Z5B2",
      "canonical_key": "shipment.gross_weight_kg",
      "field_label": "Gross weight",
      "status": "open",
      "sources": []
    }
  ],
  "decision": {
    "value": "review",
    "route": "conflict",
    "reasons": [
      "conflict_open:shipment.gross_weight_kg",
      "check_failed:INV-003"
    ]
  },
  "review": {
    "extraction_open": 1,
    "conflict_open": 1,
    "url": null
  },
  "policy": {
    "version": 3,
    "effective_at": "2026-10-01T00:00:00Z"
  }
}
```

The full document object is defined in [`06-document-schemas.md`](06-document-schemas.md). The
result is a view over a job snapshot; `documents` is always scoped to the submission, while
`conflicts` is job-wide.

`404 result_not_found` is returned for an unknown id or an id belonging to another client. The API
does not reveal whether the id exists for someone else.

## 5. Jobs, documents and conflicts

### `GET /v1/jobs/{job_id}`

Returns the logical job across every submission:

```json
{
  "job_id": "job_01J9Z3K4T7Q8M2",
  "job_ref": "BL-2026-00417",
  "status": "review_required",
  "created_at": "2026-10-31T09:12:44Z",
  "last_submission_at": "2026-11-03T07:40:12Z",
  "terminal_at": null,
  "document_count": 4,
  "documents": [
    {
      "document_id": "doc_01J9Z4A1",
      "document_type": "commercial_invoice",
      "status": "review_required",
      "decision": "review",
      "submitted_at": "2026-10-31T09:12:44Z"
    }
  ],
  "conflicts": [
    {
      "conflict_id": "cnf_01J9Z5B2",
      "canonical_key": "shipment.gross_weight_kg",
      "status": "open",
      "source_count": 2
    }
  ],
  "review": {
    "extraction_open": 1,
    "conflict_open": 1
  },
  "policy": { "version": 3 }
}
```

### `GET /v1/jobs/{job_id}/documents`

Query: `limit` (1–100, default 25), `cursor`, `status`, `document_type`.

```json
{
  "data": [
    {
      "document_id": "doc_01J9Z4A1",
      "document_type": "commercial_invoice",
      "status": "review_required",
      "decision": "review",
      "created_at": "2026-10-31T09:12:44Z"
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

### `GET /v1/jobs/{job_id}/conflicts`

Query: `limit`, `cursor`, `status` (`open`, `resolved`, `waived`).

```json
{
  "data": [
    {
      "conflict_id": "cnf_01J9Z5B2",
      "canonical_key": "shipment.gross_weight_kg",
      "field_label": "Gross weight",
      "status": "open",
      "first_seen_at": "2026-10-31T09:14:58Z",
      "sources": [
        {
          "document_id": "doc_01J9Z4A1",
          "document_type": "commercial_invoice",
          "value": "18,450.00"
        },
        {
          "document_id": "doc_01J9Z4B7",
          "document_type": "packing_list",
          "value": "18,540.00"
        }
      ]
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

### `GET /v1/documents/{document_id}`

Returns one document with fields, extension block, checks, conflicts and decision. Query
`include=provenance,checks,conflicts` controls optional sections; provenance is included by default
because it is mandatory in the contract.

A document from a different client returns `404 document_not_found`.

## 6. Policy and usage

### `GET /v1/policy`

```json
{
  "version": 3,
  "name": "2026-11 baseline",
  "effective_at": "2026-10-01T00:00:00Z",
  "invoice_pass_enabled": true,
  "trust_ramp_enabled": false,
  "review_depth": {
    "commercial_invoice": "gate",
    "proforma_invoice": "full",
    "bill_of_lading": "full",
    "delivery_order": "full",
    "port_pass": "full",
    "custom_entry": "full",
    "verification_memo": "full",
    "packing_list": "full"
  },
  "limits": {
    "max_documents_per_group": 20,
    "max_file_bytes": 26214400,
    "max_pages_per_file": 500
  }
}
```

The policy config is versioned and immutable. This endpoint exposes the non-secret operational
subset; it does not expose thresholds that would allow gaming the gate.

### `GET /v1/usage`

```json
{
  "period": { "start": "2026-10-01", "end": "2026-10-31" },
  "submissions": 214,
  "documents": 532,
  "pages": 1841,
  "limits": {
    "requests_per_minute": 120,
    "concurrent_submissions": 20,
    "documents_per_day": 1000
  },
  "rate_limit": {
    "remaining": 117,
    "reset_at": "2026-10-31T09:13:00Z"
  }
}
```

## 7. Account export and deletion

The extraction record is exportable in full at any time and deletable on request before expiry.

### `POST /v1/account/exports`

```json
{ "format": "jsonl", "include_provenance": true }
```

`202`:

```json
{
  "export_id": "exp_01J9Z6C3",
  "status": "queued",
  "status_url": "/v1/account/exports/exp_01J9Z6C3"
}
```

### `GET /v1/account/exports/{export_id}`

When complete, returns a short-lived signed download URL (15 minutes) and a SHA-256 checksum. The
export contains every document, field, provenance record, check and conflict for the client, in
JSONL, one record per line. It never contains source files (they may already be purged).

### `POST /v1/account/deletion-requests`

```json
{
  "scope": "extraction_records",
  "confirmation": "DELETE ALL EXTRACTION RECORDS",
  "reason": "Account closure"
}
```

`202` creates a deletion request. An operator verifies the request and schedules the deletion. The
response includes a `deletion_request_id` and `status_url`. Deletion is not immediate because it
must respect legal holds and backups; the effective date is stated in the response.

## 8. Errors

Every error uses one envelope:

```json
{
  "error": {
    "code": "file_too_large",
    "message": "Document 'invoice.pdf' is 31.2 MB; the limit is 25 MB.",
    "request_id": "req_01J9Z3K4T7Q8M2",
    "details": {
      "document": "invoice.pdf",
      "limit_bytes": 26214400,
      "actual_bytes": 32715571
    },
    "doc_url": "https://docs.trade-extract.example/errors#file_too_large"
  }
}
```

`message` is human-readable and safe to show. `details` is machine-readable. `request_id` is always
present and is the support handle.

| HTTP | Code | When |
|---|---|---|
| 400 | `invalid_request` | Malformed JSON, missing part, bad cursor. |
| 401 | `unauthorized` | Missing, malformed, revoked or expired key. |
| 403 | `forbidden` | Key lacks scope, or resource belongs to another client. |
| 404 | `result_not_found` | Unknown result id. |
| 404 | `job_not_found` | Unknown job id. |
| 404 | `document_not_found` | Unknown document id. |
| 409 | `idempotency_key_reused` | Same `Idempotency-Key`, different request body. |
| 409 | `job_ref_conflict` | `job_ref` belongs to another client (should be impossible; defensive). |
| 409 | `job_failed` | The `job_ref` names a failed job; use a new `job_ref`. |
| 413 | `file_too_large` | A file exceeds 25 MiB. |
| 413 | `payload_too_large` | The whole request exceeds the edge limit. |
| 415 | `unsupported_media_type` | Magic bytes are not PDF/JPEG/PNG/WebP/TIFF. |
| 422 | `too_many_documents` | More than 20 file parts. |
| 422 | `no_documents` | Zero file parts. |
| 422 | `page_limit_exceeded` | More than 500 pages in a file or 2,000 in a submission. |
| 422 | `multi_shipment_container` | Positive evidence of two or more B/L numbers. |
| 422 | `encrypted_document` | Password-protected or encrypted PDF. |
| 422 | `malformed_document` | Parser cannot read the container. |
| 429 | `rate_limited` | Per-key request or concurrency limit. |
| 500 | `internal_error` | Unhandled server error. |
| 503 | `service_unavailable` | Database, storage or maintenance outage. |

`multi_shipment_container` example:

```json
{
  "error": {
    "code": "multi_shipment_container",
    "message": "This group contains 2 shipments (B/L KEMU123456789 and KEMU987654321). Split it and resubmit one shipment per job_ref.",
    "request_id": "req_01J9Z3K4T7Q8M2",
    "details": { "shipment_count": 2, "bill_of_lading_numbers": ["KEMU123456789", "KEMU987654321"] }
  }
}
```

## 9. Rate limits and quotas

| Limit | Default | Enforced |
|---|---|---|
| Requests per API key | 120/min | Middleware token bucket + DB daily counter. |
| Concurrent submissions | 20 per client | Count of `queued` + `processing` submissions. |
| Documents per day | 1,000 per client | `client_usage_daily`. |
| Documents per group | 20 | Request validation. |
| File size | 25 MiB | Request validation. |
| Pages per file / group | 500 / 2,000 | Post-parse validation. |

Responses include:

```http
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 117
X-RateLimit-Reset: 1793524380
```

`429` includes `Retry-After` in seconds. Limits are configuration, not code constants. A client
that needs more gets a v2 chunking story, not a raised limit.

## 10. Webhooks

Completion is delivered to the submission's `webhook_url`, or the client default. Polling is always
available as a fallback.

Event types:

| Event | When |
|---|---|
| `result.completed` | Every submitted document reached a terminal outcome and the result is readable. |
| `result.review_required` | The result is readable but at least one review item or conflict is open. |
| `result.failed` | The submission failed before producing a usable result. |

Payload:

```json
{
  "id": "evt_01J9Z7D4",
  "type": "result.completed",
  "created_at": "2026-10-31T09:15:03Z",
  "data": {
    "result_id": "res_01J9Z3K4T7Q8M2",
    "job_id": "job_01J9Z3K4T7Q8M2",
    "job_ref": "BL-2026-00417",
    "status": "completed",
    "decision": "review",
    "document_count": 3,
    "conflict_count": 1,
    "review": { "extraction_open": 1, "conflict_open": 1 },
    "result_url": "https://api.trade-extract.example/v1/results/res_01J9Z3K4T7Q8M2"
  }
}
```

The payload deliberately does not embed document content. The client fetches the result with its API
key, which keeps webhook logs and third-party relays from becoming a second retention policy.

Headers:

```http
X-TradeExtract-Event: result.completed
X-TradeExtract-Delivery: whd_01J9Z7D4
X-TradeExtract-Timestamp: 1793524503
X-TradeExtract-Signature: v1=5f2c…
Content-Type: application/json
```

Signature:

```text
HMAC-SHA256(webhook_secret, timestamp + "." + raw_request_body)
```

Verify with a constant-time comparison and reject timestamps older than five minutes. The secret is
per client, rotatable, and never sent in the payload.

Delivery rules:

- HTTPS only in production; the URL is resolved and private, loopback, link-local and metadata
  addresses are blocked. Redirects are not followed.
- Connect timeout 5 s, read timeout 10 s.
- Retries: 1 min, 5 min, 30 min, 2 h, 6 h, 12 h, 24 h, 24 h (8 attempts).
- `2xx` marks delivered. `410` marks dead immediately. `408` and `429` retry. Other `4xx` retry
  twice, then dead. `5xx` retries to the limit.
- Dead deliveries are visible in the review console and can be replayed by an operator.
- Delivery is at-least-once; clients must deduplicate on `id`.

## 11. Versioning and deprecation

- `/v1` is the stable public surface. Additive fields may appear without a version bump; clients must
  ignore unknown fields.
- Removing or changing a field requires `/v2`.
- Deprecations are announced with `Deprecation` and `Sunset` headers and a minimum six-month
  overlap.
- Each document type carries its own `schema_version` (for example `invoice.peppol-1.0`). A schema
  change that alters meaning is a new schema version, even within `v1`.
- The OpenAPI document is the contract test; CI fails if the generated schema drifts from the
  committed `openapi.yaml` without a review.

## 12. OpenAPI

The OpenAPI 3.1 document in [`openapi.yaml`](openapi.yaml) covers the public endpoints, security
schemes, error envelope, pagination, and the document result envelope. Internal review and admin
routes are intentionally excluded from the public document.

Generate client SDKs from `openapi.yaml`, not from a running server. The committed file is the
source of truth for external integrators.
