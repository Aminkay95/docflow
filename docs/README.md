# Trade Extract — Design Documentation

Trade Extract is a provenance-first, asynchronous extraction API for Kenyan trade documents.
A caller submits a group of one or more files under an optional opaque `job_ref`; the service
splits every file into document segments, extracts **stated** values with page and bounding-box
provenance, runs deterministic checks, reports cross-document conflicts, and decides whether the
result may pass or must be reviewed.

> **Status:** design documentation only. No application code has been built. Everything in this
> folder is the contract the build phase will implement.

## Source of truth

These documents are derived from:

- [`../_bmad-output/specs/spec-trade-doc-extraction/SPEC.md`](../_bmad-output/specs/spec-trade-doc-extraction/SPEC.md)
- [`../_bmad-output/specs/spec-trade-doc-extraction/stack.md`](../_bmad-output/specs/spec-trade-doc-extraction/stack.md)
- [`../_bmad-output/specs/spec-trade-doc-extraction/document-types.md`](../_bmad-output/specs/spec-trade-doc-extraction/document-types.md)
- [`../_bmad-output/specs/spec-trade-doc-extraction/provider-notes.md`](../_bmad-output/specs/spec-trade-doc-extraction/provider-notes.md)
- [`../_bmad-output/specs/spec-trade-doc-extraction/stories.yaml`](../_bmad-output/specs/spec-trade-doc-extraction/stories.yaml)
- [`../_bmad-output/forge/doc-extraction-pipeline/forged-idea.md`](../_bmad-output/forge/doc-extraction-pipeline/forged-idea.md)

The SPEC wins over these documents. Where a design choice is not fixed by the SPEC, the decision
is recorded in [`08-operations-and-decisions.md`](08-operations-and-decisions.md).

## Reading order

| # | Document | What it answers |
|---|---|---|
| 1 | [`01-architecture.md`](01-architecture.md) | How the system works, component by component, and why. |
| 2 | [`02-file-structure.md`](02-file-structure.md) | Exact repository layout and module boundaries. |
| 3 | [`03-database-schema.md`](03-database-schema.md) | Tables, relationships, indexes, state machines, retention. |
| 4 | [`04-api.md`](04-api.md) | Public API contract, errors, webhooks, idempotency, limits. |
| 5 | [`05-ui-architecture.md`](05-ui-architecture.md) | Internal review console: routes, screens, HTMX patterns, security. |
| 6 | [`06-document-schemas.md`](06-document-schemas.md) | Output envelope, provenance, and the seven (plus one proposed) document schemas. |
| 7 | [`07-implementation-blueprint.md`](07-implementation-blueprint.md) | Build order, key code contracts, testing, Docker, CI. |
| 8 | [`08-operations-and-decisions.md`](08-operations-and-decisions.md) | Runbook, SLOs, ADR log, spec ambiguities, risks. |
| — | [`openapi.yaml`](openapi.yaml) | Machine-readable public API contract. |

## The shape in one page

```text
Client ──HTTPS──► api (FastAPI) ──► Postgres 16
                    │                  │
                    │                  ├─ jobs / submissions / files / pages / segments
                    │                  ├─ documents / field_values / checks / conflicts
                    │                  └─ job_tasks (durable queue, SKIP LOCKED)
                    │
                    ├──► object storage (S3/R2 in prod, filesystem in dev)
                    │
                    └──► worker (PaddleOCR + OpenCV + pypdfium2)
                              │
                              ├──► DeepSeek adapter (vision: deepseek-flash; text: deepseek-v4-pro)
                              ├──► deterministic checks and conflict detection
                              └──► webhook outbox
```

One language (Python 3.12), two runtime images (`api`, `worker`), one stateful dependency
(PostgreSQL), and one storage interface with filesystem and S3 implementations.

## The five invariants

1. **As stated, never computed.** Every emitted value is what the page says. Arithmetic is a test
   oracle, not a data source. No duty, levy, VAT or CIF is calculated.
2. **Provenance is mandatory.** A field without page index and bounding box is not emitted.
3. **Confidence never gates.** Only deterministic checks and falsifiable signals decide pass or
   review. Model confidence may order the review queue.
4. **Conflicts are reported, never resolved.** Two documents may disagree; the platform names the
   field and both sources and leaves the business call to the client.
5. **Unreadable is a valid outcome.** A page that cannot be read after conditioning returns a reason,
   never a plausible guess.

## Key scope decisions

| Decision | Choice |
|---|---|
| Market | Kenya: KRA iCMS, KenTrade TradeNet, KPA / KWATOS. National context is data, not a field name. |
| Grouping | Caller-supplied `job_ref`; the platform never infers or regroups. |
| Intake | Caller-side API only. No mailbox, no grouper, no ERP posting in v1. |
| Async contract | `POST /v1/extract` → `202` + `result_id`; webhook with polling fallback. |
| Document types | Seven named types, plus a **proposed** `packing_list` type required by CAP-2 and CAP-11. See [ADR-013](08-operations-and-decisions.md#adr-013-packing-list-is-a-proposed-eighth-type). |
| Review | Two separate queues: extraction errors (our fault) and document conflicts (the client's call). |
| Trust | Records are written in v1; no automatic promotion. |
| Retention | Files purged six hours after a terminal state; extraction records outlive files. |
| UI | Server-rendered Jinja2 + HTMX, served by the API. No SPA, no build step. |

## What is deliberately not here

No Kubernetes, message broker, Redis, vector database, prompt framework, second language, on-prem
stack, ERP posting, duty calculation, email ingestion, or fuzzy job matching. Each is defensible
later; none is earned at v1 volume.

## Glossary

| Term | Meaning |
|---|---|
| **File** | The uploaded container (PDF or image). A file has pages. |
| **Page** | The atom of reading. One page image, one text layer, one legibility score. |
| **Segment** | A contiguous page range classified as one document type. The unit of extraction. |
| **Document** | A segment plus its extraction record: fields, provenance, checks, decision. |
| **Job** | The logical accumulator for a `job_ref`. May receive documents across many submissions. |
| **Submission** | One `POST /v1/extract` call. Has one `result_id`. |
| **Result** | The per-submission view: submitted documents, job conflicts, decision. |
| **Conflict** | Two documents in one job state different values for the same canonical field. |
| **Review item** | A queue entry, on exactly one track: extraction or conflict. |
| **Terminal** | A job state that starts the six-hour file-retention clock: completed or failed, with no open review item or conflict. |
| **Provenance** | Page index, normalized bounding box, reader, and source hash for a value. |
