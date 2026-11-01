---
title: 'Page model and always-on segmentation'
type: 'feature'
created: '2026-11-01'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
baseline_revision: 'da605834ba9d494fa2ee3989a3e0174815250e1a'
context:
  - 'docs/01-architecture.md'
  - 'docs/02-file-structure.md'
  - 'docs/03-database-schema.md'
  - 'docs/06-document-schemas.md'
warnings: ['oversized']
deferred:
  - summary: >-
      The file row lock and its database transaction are held across the whole preparation
      pipeline, including source download, full rasterisation, two storage uploads per page and
      segmentation.
    evidence: |-
      `prepare_file.handle` calls `files_repo.get_for_update` before `_download_source` and
      `_render_and_store`, and the runner commits only after the handler returns. A large PDF at
      300 DPI can therefore hold the lock and a pooled connection for minutes. Correctness is
      preserved (two deliveries cannot write two page sets) and the documented v1 deploy runs one
      worker, but throughput and connection-pool pressure need a later redesign — render outside
      the lock, then lock and re-check for an existing page set before writing.
    location: >-
      src/trade_extract/workers/handlers/prepare_file.py:136
    severity: low
---

<intent-contract>

## Intent

**Problem:** An accepted file is still an opaque container: the worker marks it `processed` without creating page rows, and every completed result carries an empty `documents` array. A PDF holding a bill of lading, an invoice and a packing list therefore still produces no documents, so CAP-2 cannot be demonstrated.

**Approach:** Add the page atom — rendered image, thumbnail, embedded text and geometry — and run deterministic, always-on segmentation inside `prepare_file`, persisting non-overlapping segments and exactly one document per segment. Surface those documents on the existing polling result with correct types, page ranges and source identity.

## Boundaries & Constraints

**Always:**
- Every page of every accepted file gets exactly one `pages` row keyed by `(file_id, page_index)`, carrying a rendered image key, thumbnail key, pixel width/height, DPI, rotation, embedded-text facts and status `pending` (conditioning is story 5).
- Page ranges are 0-based inclusive. For each file, segments must cover pages `0..N-1` exactly once, with no overlap and no gap; the `segments` exclusion constraint is the final database guard.
- Segmentation runs unconditionally on every file, on the worker, before that file can be marked `processed` and before its submission can complete. It reads only the file's own pages.
- Classification uses the page's embedded text and nothing else. Recognised types are `commercial_invoice`, `proforma_invoice`, `bill_of_lading`, `delivery_order`, `port_pass`, `custom_entry`, `verification_memo`, `packing_list`, plus `unknown`.
- A page whose deterministic top score is below the configured confidence floor, tied with another type, or absent (no text) becomes `unknown`, unless it is a continuation of a confident previous page, in which case it inherits that page's type with continuation confidence.
- Every segment yields exactly one `documents` row with `status` `queued` and segment `status` `pending`. `document_hash = sha256("<file_sha256_hex>:<page_start>:<page_end>:<document_type>")` and `idempotency_key` is the story-1 per-document key; both must be deterministic and unique per job. Stored hashes are bare 64-character hex; the result renders them as `sha256:<hex>`.
- A polling result lists one object per document row for the submission's files, with `document_type`, `document_type_confidence`, `source` (filename, content type, hashes, page range, segment page count) and `status`. `include` and `document_id` keep working.
- Preparation is idempotent: re-running `prepare_file` on a file whose pages, segments and documents already exist creates no second set of rows or objects; reprocessing a failed/resurrected file replaces its prior page/segment/document set atomically.
- Every rendering, thumbnail, confidence and classifier knob comes from `Settings`; no limit is a literal in a handler.
- A preparation failure (unreadable source, render failure, storage error) is terminal through the existing retry/dead path and leaves the submission `failed` with a reason naming the file.

**Never:**
- No field extraction, schema validation, checks, decisions, conflicts, review items, OCR or vision model calls in this story. Documents stay `queued` with null extraction.
- Never classify from filename, declared content type, `job_ref`, client hints, or another file in the group.
- Never infer a job or document relationship across files; segmentation splits one file only.
- Never guess a type for an image-only or unreadable page. No text yields one `unknown` segment covering the file; it is not a fabricated type.
- Never re-list an earlier file's documents for a duplicate part in the new submission; `duplicate_documents` still reports duplicates. Cross-submission accumulation is story 11.
- No orientation correction, deskew, crop, denoise or legibility scoring; that is story 5. Store the page as rendered.
- No `Base.metadata.create_all` inside a migration; the new table is frozen forward-only DDL.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| MIXED_FILE | 3-page text PDF: B/L, invoice, packing list | 3 documents, ranges `0-0`, `1-1`, `2-2`, types `bill_of_lading`, `commercial_invoice`, `packing_list`; no overlap, full coverage | — |
| MULTIPAGE_TYPE | 4 pages: B/L page, invoice pages, packing list page | 3 documents; invoice range `1-2` | — |
| CONTINUATION | Page 2 has body text but no title keywords | Page 2 inherits the confident type of page 1 and merges into its segment | — |
| IMAGE_ONLY | One JPEG with no text layer | 1 page row, no text; 1 `unknown` document range `0-0` | — |
| MULTIFRAME_TIFF | 3-frame TIFF | 3 page rows, one `unknown` document range `0-2` | — |
| AMBIGUOUS_PAGE | Two types score equally on a file's first page | That page and its run are `unknown` | — |
| FILENAME_LIE | File named `invoice.pdf` whose text is a B/L | Type is `bill_of_lading` | — |
| RETRY_AFTER_SUCCESS | `prepare_file` re-armed for a processed file | No new page/segment/document row and no duplicate document in the result | — |
| RESURRECTED_FILE | Failed file resubmitted with the same bytes | Prior page/segment/document rows are replaced; exactly one new set exists | — |
| RENDER_FAILURE | Stored source missing or undecodable | File retries then dies; submission `failed` with a reason naming the file; no documents | `processing_failed` reason on the failed result |
| DUPLICATE_FILE | Same bytes resubmitted under one `job_ref` | No new rows and no re-listed documents; `duplicate_documents` increments | — |
| DOC_FILTER | `document_id` names one document / an unknown id | One document returned / empty list | — |

</intent-contract>

## Code Map

- `migrations/versions/0002_pages.py` — new revision after `0001_initial`: create `pages` (docs/03 DDL plus `text_layer text`), `UNIQUE (file_id, page_index)`, `ix_pages_file`, `ix_pages_status`, and `trg_pages_updated_at`; downgrade drops the table. `0001_initial` stays frozen.
- `src/trade_extract/db/models/page.py` — SQLAlchemy `Page` mirroring that DDL exactly so `alembic check` stays clean; register in `db/models/__init__.py` so `Base.metadata` (and `clean_database` truncation) includes it.
- `src/trade_extract/services/page_model.py` — existing `PageProbe`, `ContainerProbe`, `probe_container()`, `probe_image()`; extend with `render_container()` yielding per-page renderings (image bytes, thumbnail bytes, width/height/DPI, text) one page at a time so memory stays bounded, keeping `pypdfium2`/Pillow imports lazy so the API image stays reader-free.
- `src/trade_extract/services/segmentation.py` — new: weighted type lexicon, `classify_page(text)` and `segment_pages(page_texts, ...)`; deterministic confidence, continuation inheritance, contiguous grouping, coverage check, layout fingerprint.
- `src/trade_extract/services/storage.py` — add `page_image_key(...)` / `page_thumbnail_key(...)` beside `source_key()`; reuse `ObjectStorage` protocol and both adapters unchanged.
- `src/trade_extract/db/repositories/{pages,segments,documents}.py` — new: insert/list/delete-for-file, `get_for_update` for the file lock, and `create_for_segment`.
- `src/trade_extract/db/repositories/files.py` — add `get_for_update()`; existing `mark_processing/mark_processed/mark_failed` stay.
- `src/trade_extract/services/ingest.py` — `build_result()` currently returns empty `documents`; join document+segment+file rows, apply `include`/`document_id`, and derive `documents_total/completed/failed` from document rows when they exist.
- `src/trade_extract/workers/handlers/prepare_file.py` — replace the stub body with fetch → probe → render/upload → segment → persist pages/segments/documents → keep the existing finalize fan-in; build storage with `build_storage(settings)`, download the source to a temp file with cleanup on every path, make preparation idempotent and transaction-safe.
- `src/trade_extract/domain/errors.py` — add `processing_failed(document, reason)` for worker-side failures; keep messages actionable.
- `src/trade_extract/config.py`, `.env.example` — add `page_render_dpi=300`, `page_thumbnail_max_px=512`, `page_image_quality=85`, `segmentation_min_confidence=0.55`; document them.
- `docs/03-database-schema.md` — add the `pages.text_layer` column to the DDL so the blueprint matches the migration.
- `tests/integration/test_migrations.py` — `TRIGGER_NAMES` gains `trg_pages_updated_at`; expected table list gains `pages`.
- `tests/unit/test_segmentation.py`, `tests/unit/test_page_model.py` — classifier/grouping/fingerprint and rendering/text-layer units.
- `tests/integration/test_pages_segmentation.py` — end-to-end page, segment and document surfaces; `tests/integration/test_lifecycle.py` — update the story-1 empty-documents assertion.

## Tasks & Acceptance

**Execution:**
- `migrations/versions/0002_pages.py`, `src/trade_extract/db/models/page.py`, `src/trade_extract/db/models/__init__.py`, `docs/03-database-schema.md` — add the page atom as literal forward-only DDL and a matching model; create the `updated_at` trigger and remove it with the table on downgrade.
- `tests/integration/test_migrations.py` — assert the pages table, its two indexes and all seven `updated_at` triggers survive upgrade and that downgrade leaves nothing behind.
- `src/trade_extract/services/page_model.py` — add `render_container(path, content_type, *, document, dpi, thumbnail_max_px, quality)` yielding ordered page renderings one page at a time, with image bytes, thumbnail bytes, pixel size, DPI and embedded text; PDF pages are rasterised with pypdfium2, image containers frame by frame with Pillow, and all heavy imports stay inside the functions.
- `src/trade_extract/services/segmentation.py` — implement the weighted lexicon for the eight recognised types, per-page scoring and 0–1 confidence, a configurable floor, title-vs-body weights, continuation inheritance, contiguous run grouping, contiguous coverage validation, and a stable layout fingerprint over the segment's first page with digit runs replaced by `#`. A merged segment's confidence is the minimum confidence of its pages.
- `src/trade_extract/services/storage.py` — add page image and thumbnail key helpers following the existing `clients/<client>/jobs/<job>/files/<file>/...` scheme.
- `src/trade_extract/db/repositories/pages.py`, `segments.py`, `documents.py`, `files.py` — persistence primitives: create/delete all rows for a file, list documents for a submission with their segments and files, and lock the file row while it is prepared.
- `src/trade_extract/workers/handlers/prepare_file.py` — lock the file; no-op when its page/segment/document set already exists; otherwise mark processing (and submission/job processing), download the source to a temp file, probe, render and upload page images/thumbnails, segment, then in one transaction replace the file's page/segment/document rows, mark the file processed, and re-arm `finalize_submission` when siblings are terminal; always delete the temp file and map failures through `processing_failed`.
- `src/trade_extract/domain/errors.py`, `src/trade_extract/config.py`, `.env.example` — add the failure type and the four settings with validated defaults/ranges.
- `src/trade_extract/services/ingest.py` — assemble `documents` from persisted rows: envelope ids, type, confidence, `status`, `source` with `sha256:<hex>` hashes rendered from the stored 64-hex values, page range and segment page count, null fields/checks/decision, and progress counts derived from documents when present; keep duplicate counting unchanged.
- `tests/unit/test_segmentation.py` — every matrix classification case, multi-page grouping, continuation, ties, no-text files, full coverage/no-overlap invariants, confidence bounds, and fingerprint stability across value changes.
- `tests/unit/test_page_model.py` — rendered page count/size for a made PDF, per-page text capture, multi-frame TIFF frame rendering, and thumbnail bounds.
- `tests/integration/test_pages_segmentation.py`, `tests/integration/test_lifecycle.py` — matrix end-to-end cases, storage objects exist and decode, idempotent retry, resurrected-file replacement, render failure terminalisation, document filtering, an overlapping-segment insert rejected by the exclusion constraint, and the updated story-1 completion assertion.

**Acceptance Criteria:**
- Given a 3-page text PDF containing a bill of lading, a commercial invoice and a packing list, when it is submitted and the worker drains, then the completed result lists exactly three documents whose page ranges are `0-0`, `1-1` and `2-2`, whose types match the pages, and whose ranges are non-overlapping and cover every page.
- Given a 4-page PDF of a bill of lading, a two-page invoice and a packing list, when segmentation completes, then exactly three documents exist and the invoice spans pages `1-2` as one segment.
- Given any accepted file, when preparation completes, then it has one `pages` row per source page, each with a stored image and thumbnail that decode, and every page belongs to exactly one segment.
- Given an image-only file, when preparation completes, then it yields page rows with no text layer and exactly one `unknown` document spanning the file rather than a guessed type.
- Given a processed file whose `prepare_file` task is re-armed, when the worker runs, then no page, segment or document row is added and the polled documents are unchanged.
- Given a failed file resubmitted with identical bytes, when the worker reprocesses it, then the old page/segment/document rows are gone and exactly one fresh set exists.
- Given a source object that cannot be read, when preparation exhausts its retries, then the submission becomes `failed`, the failed result names the file and reason, and no document rows exist for it.
- Given a completed result, when `include=documents` and `document_id` are used, then only the requested document is returned, every document reports `document_type_confidence` between 0 and 1, and every document's `source.document_hash` equals `sha256("<file_sha256_hex>:<page_start>:<page_end>:<document_type>")`.
- Given two segment rows inserted for one file with overlapping page ranges, when the second insert commits, then PostgreSQL rejects it through `ex_segments_page_range`.

## Spec Change Log

## Review Triage Log

### 2026-11-01 — Review pass
- verdicts: 41 findings — high 0, medium 6, low 30, false 5, maybe-false 0
- findings:
  - `[low]` `[patch]` docs/01 still showed a `segment_file` claim and 200 DPI thumbnails — updated the sequence diagram to inline segmentation and documented long-edge thumbnails.
  - `[low]` `[patch]` docs/08 sized storage as “page PNG” — corrected to the quality-85 JPEG derivative and thumbnail.
  - `[medium]` `[patch]` Queued documents emitted `tables: null` and `extension: null` although the committed contract types both as objects — now emit `{}`.
  - `[false]` `[reject]` `schema_version` is null for a typed document — `docs/openapi.yaml` allows `string|null`; population belongs to story 3's schemas.
  - `[medium]` `[patch]` One weak body signal (for example only `DUTY`, raw score 0.5) classified at confidence 1.0 — the raw top score must also meet the floor; weak-keyword unit test added.
  - `[low]` `[reject]` Continuation confidence `0.6` is a literal, not `Settings` — only bites when an operator raises the floor above 0.6; adding config surface for a classifier curve the default never crosses is not worth it, and the continuation policy is revisited in stories 4–5.
  - `[low]` `[reject]` A tied page after a confident page is absorbed by continuation inheritance — that inheritance is the intent-contract's explicit rule; the §6.3 vision fallback arrives with story 4.
  - `[low]` `[reject]` Two consecutive same-type documents merge into one segment — the intent's success case is several *types*; same-type boundary detection needs reader/fingerprint signals (stories 4, 7, 14).
  - `[medium]` `[patch]` Progress reported `3/3` while a sibling file was still processing and dropped duplicate parts — pending and failed files without documents plus `duplicate_count` now count in total, and duplicates in completed.
  - `[low]` `[reject]` Webhook `document_count` and `jobs.document_count` still count parts, not segments — the webhook field is story 1's submission-part contract and job counters are maintained by the accumulation story (11); this story does not own that reconciliation.
  - `[low]` `[patch]` The post-write terminal-submission guard was unreachable after the earlier guard and `mark_processing` — removed with its stale comment.
  - `[low]` `[defer]` The file row lock and transaction are held across download, full rasterisation, storage uploads and segmentation — deferred as concurrency tuning; the documented v1 deploy runs one worker, and correctness (never two sets) is preserved.
  - `[false]` `[reject]` The PDF is parsed twice and `handle` never calls the probe — the accept-time probe must run in the API process before storage, the worker must re-open the stored source to render it, and the new `text_layer` column serves downstream readers; encrypted PDFs are rejected at intake, so the error-mapping difference is unreachable.
  - `[low]` `[patch]` Image pages claimed the configured render DPI although frames keep native pixels — source DPI metadata is used when present, with a test.
  - `[false]` `[reject]` Lossless PNG/TIFF originals are discarded by JPEG re-encoding — the source object is retained unchanged; the JPEG is a derived page image that story 5 can re-render from the source.
  - `[low]` `[reject]` Named model checks differ from the unnamed DDL, `ix_pages_file` duplicates the unique index, and “nothing runs `alembic check`” — the migration test does call `command.check`; Alembic cannot compare CHECK constraints (story 1's logged limit), and the redundant index is inherited from the documented DDL.
  - `[low]` `[patch]` Coverage gaps around `text_layer`, `tables`/`extension`, `include=documents`, the exclusion-constraint name and thumbnail bounds — assertions added; the per-document key formula is already covered by story 1's hashing unit test.
  - `[low]` `[reject]` mypy omits workers and repositories — the Verification section deliberately scopes the typed core (`domain`, `services`), following story 1; the fix would be a spec edit.
  - `[medium]` `[patch]` PDF rendering had no pixel budget, so a large page at a high configured DPI could exhaust the worker — a 100-megapixel pre-render guard raises `processing_failed`, with a 1200 DPI test.
  - `[false]` `[reject]` `processing_failed` uses `http_status=500` — it is raised only inside a worker task and surfaces as a task error and failed decision reason; no API route maps it to a response.
  - `[low]` `[reject]` The purged-file resurrection path and idempotency fixture are not exercised exactly — resurrection takes the same handler path as `failed`; the fixture asserts the schema's unique key, and the free page range is deliberate.
  - `[low]` `[reject]` `layout_fingerprint` uses only the first page and empty text hashes identically — the fingerprint is opaque and unconsumed until story 13's trust records, which can refine or recompute it.
  - `[medium]` `[patch]` (grouped with progress) Duplicate parts disappeared from totals when document rows existed — `duplicate_count` is now included in `documents_total` and `documents_completed`, with a duplicate-plus-new test.
  - `[low]` `[reject]` (grouped with fingerprint) All textless segments share one fingerprint — no consumer exists; story 13 owns template identity.
  - `[low]` `[reject]` `rotation_deg` records the PDF rotation PDFium already applied, risking a double rotation later — it is source metadata for story 5's orientation policy, and nothing consumes it yet.
  - `[low]` `[patch]` (grouped with image DPI) Image containers misstated the render DPI — fixed through source metadata.
  - `[low]` `[reject]` (grouped with continuation confidence) The continuation confidence is a fixed literal below a raised floor — default behaviour is sound; revisit with the story 4 routing/vision work.
  - `[low]` `[patch]` (grouped with unreachable guard) The post-write guard could never fire — removed.
  - `[low]` `[patch]` The thumbnail bound test used a 16×16 source with a 16 px cap, so a no-op passed — now renders a 612×792 page at 72 DPI with a 64 px cap and asserts exact long edge and aspect ratio.
  - `[low]` `[patch]` The rearm test only re-armed after completion, hiding a broken processed-file guard — a two-file sibling-active no-op test with unchanged document ids was added.
  - `[low]` `[patch]` Partial success/failure progress had no test — a mixed prepared/failed submission now asserts `2/1/1` and only the successful document.
  - `[low]` `[patch]` The Settings confidence floor and image quality were never observed off their defaults — a floor-0.9 integration case and a quality byte-length unit test were added.
  - `[low]` `[patch]` (grouped with image DPI) The end-to-end suite never checked an image page's DPI — covered by the source-DPI unit test.
  - `[low]` `[reject]` Classification is tested only on lexicon titles, not real documents — real-document accuracy is story 7's labelled corpus by intent; the deterministic rules are unit-tested.
  - `[low]` `[reject]` A tied/low-confidence page is inherited rather than sent to a vision classifier or `unknown` — the intent-contract's continuation rule governs; the architecture's vision fallback is story 4, and docs/01 now records the inline slice.
  - `[medium]` `[patch]` (grouped with weak-signal) Confidence was vacuous for a single-signal page — the raw-score floor makes the number and the outcome meaningful.
  - `[low]` `[reject]` Duplicate parts are exempt from “segmentation runs on every file” — re-segmenting a duplicate would duplicate output; story 11 owns cross-submission accumulation.
  - `[low]` `[reject]` The `segment_file` stage has no counterpart in the diff — inline segmentation is the documented slice decision (Design Notes), the task kind is preserved for story 6, and docs/01 was corrected.
  - `[low]` `[reject]` The overlap test inserts page 10 on a one-page file — the AC is that the database rejects overlap through `ex_segments_page_range`, which that test now asserts; producer coverage is validated by `validate_coverage` units.
  - `[low]` `[patch]` The result/progress contract changes were thinly tested — partial, duplicate, `tables`/`extension` and explicit `include=documents` tests were added.
  - `[false]` `[reject]` The page atom exists only as a schema and rows with no consuming stage — story 2's deliverable is exactly the page atom and segmentation; readers and extraction are later stories.

## Design Notes

- **Segmentation is inline in `prepare_file`.** It is deterministic and cheap relative to extraction (SPEC constraint), so a separate `segment_file` claim adds a task, a race with `finalize_submission`, and no safety. The task kind remains available for story 6's routing work. Existing story-1 task counts (two per file) stay valid.
- **Documents are `queued` after segmentation, and the submission still completes.** This build ends at segmentation; extraction arrives in stories 4 and 8. `completed` means every stage implemented in this slice has run, and each document object exists. Progress counts document output objects when present, so a mixed file reports `3/3`, not `1/1`; when no document rows exist (a duplicate-only submission) story-1 count semantics stay; `documents_failed` adds failed files that produced no documents.
- **`pages.text_layer` is a deliberate blueprint addition.** docs/03 omitted a text column, but the page atom is where embedded text belongs and re-parsing the source for every classifier/reader is wasteful; the column is purged with the page.
- **Thumbnails are capped by long edge, not rendered at 200 DPI.** docs/01's "200 DPI for thumbnails" produces a second near-full-size raster; a 512 px long edge is the actual intent of a thumbnail. Recorded as a deliberate deviation.
- **Classifier and fingerprint.** Pages are scored by weighted title/body patterns; confidence is the top type's share of the top-two score, so a tie is low confidence. A continuation page inherits the previous confident type at a fixed 0.6 confidence and merges into its segment; a tie or missing score with no confident predecessor is `unknown`. Fingerprint hashes the first page's normalised line skeleton with digit runs replaced by `#`, so the same template with different values matches — the signal story 13's trust records need.
- **Ordering and crash safety.** Images are uploaded before the single row-writing transaction, so a crash leaves at most orphan images (the retention sweeper owns orphan cleanup, as deferred in story 1) and never a half-written page/segment set. Reprocessing deletes the prior set in the same transaction that writes the new one.

## Verification

**Commands:**
- `uv sync --extra dev` — expected: resolves and installs.
- `uv run ruff check .` and `uv run ruff format --check .` — expected: clean.
- `uv run mypy src/trade_extract/domain src/trade_extract/services` — expected: no errors.
- `uv run pytest -q` — expected: unit, contract and new segmentation/page-model suites pass without external services.
- `docker compose up -d postgres && uv run pytest -q -m integration` — expected: migration, lifecycle, page/segmentation and existing integration suites pass against PostgreSQL 16.
- `uv run pytest -q tests/integration/test_migrations.py` — expected: upgrade creates `pages`, its indexes and trigger; `alembic check` reports no drift; downgrade to base leaves no tables.

**Manual checks (if no CLI):**
- Submit a three-page mixed PDF, poll the result and confirm three document objects with the expected types and ranges; inspect `var/storage` for one image and one thumbnail per page.

## Review Triage Log

### 2026-11-01 — Follow-up review pass
- verdicts: 22 findings — high 0, medium 3, low 15, false 4, maybe-false 0
- intent-alignment: descriptive audit only — no findings; the intent's outcomes live on the polling surface and row invariants, while the contestable choices live in services, which is the expected shape for this slice.
- findings:
  - `[low]` `[patch]` An `unknown` page could report confidence 1.0 — a page rejected only for weak absolute evidence (for example `DUTY`, raw score 0.5) returned `unknown` with share 1.0; `classify_page` now reports 0.0 for that case and `test_one_weak_body_signal_is_unknown` asserts it.
  - `[low]` `[patch]` One setting gated two units — `SEGMENTATION_MIN_CONFIDENCE` was documented only as the top type's share but also gates the raw weighted score; `.env.example`, `config.py` and the `classify_page` docstring now name both gates.
  - `[low]` `[reject]` The boundary statement "every confidence and classifier knob comes from Settings" is contradicted by `CONTINUATION_CONFIDENCE`, `_TITLE_LINE_COUNT` and the lexicon — the fix is an edit to the shipped intent-contract's boundary wording, which triage rejects; the continuation-constant part was already adjudicated in the previous pass.
  - `[low]` `[reject]` `documents_total` mixes segment, part and duplicate units — the Design Notes deliberately define progress as document objects plus unrepresented parts (the previous pass added duplicate counting), the intent does not constrain progress semantics, and the total reconciles once every part is terminal.
  - `[false]` `[reject]` Queued documents counted as "completed" mislead clients — no documented surface defines `documents_completed` as extraction progress; documents carry `status: queued` and null extraction, and the Design Notes define completion as "every stage implemented in this slice has run".
  - `[low]` `[patch]` The `pending_without_documents` progress branch had no test — the verification-gap layer filed the same gap; `test_progress_counts_a_sibling_that_is_still_waiting` now asserts `2/1/0` while `processing` and `2/2/0` after the drain.
  - `[medium]` `[patch]` docs/04 still promised one output object per submitted document including duplicates — corrected to one per segment, with duplicates reported only through `duplicate_documents`.
  - `[medium]` `[patch]` docs/01 §6.3 still described a vision fallback below threshold and routed unknown pages to review — rewritten to the embedded-text-only classifier, continuation inheritance, leading `unknown` pages and the later vision slice.
  - `[low]` `[reject]` docs/06 types `schema_version` as a string and `checks` as an array while queued documents emit null — docs/06 describes the post-extraction envelope, queued is transitional, and `docs/openapi.yaml` (the client contract) allows null, as the previous pass established.
  - `[medium]` `[patch]` docs/06 documented `document_hash` as bare `file_hash + page_start + page_end + document_type` — corrected to the exact `sha256("<file_sha256_hex>:<page_start>:<page_end>:<document_type>")` with a 0-based inclusive range and the `sha256:<hex>` rendering.
  - `[low]` `[reject]` docs/02's tree omits the new page/repository/test modules and still lists `segment_file.py` — docs/02 is the aspirational whole-project tree (it lists many modules this slice deliberately does not build), the task kind is retained for story 6, and the previous pass rejected the same `segment_file` claim.
  - `[false]` `[reject]` Nothing ties `pages` rows to `files.page_count` — both derive from the same immutable stored bytes through the same parser, the source key is per file id and written once, so probe/render disagreement needs an external object mutation the platform never performs.
  - `[false]` `[reject]` `_render_image` has no pixel budget — the intake probe already rejects any image frame above 100 megapixels before the object is stored and the renderer decodes those same bytes; the PDF budget exists because configured DPI can enlarge a page beyond its source pixel count.
  - `[low]` `[reject]` EXIF orientation is not recorded, so `rotation_deg` is 0.0 for a sideways photo — orientation policy is story 5's, the field is unconsumed, the original source is retained for a later re-read, and reading or normalising EXIF here is more than a direct correction.
  - `[low]` `[reject]` `test_classification_never_reads_a_filename` cannot catch a filename regression — `classify_page` has no filename parameter to regress through, and `test_filename_does_not_decide_the_document_type` guards the end-to-end rule.
  - `[false]` `[reject]` The render-failure fixture's mangled filename and the quality test's length inequality prove nothing — the uploaded name really is `unreadable.pdf-0.pdf`, so the assertion names the uploaded file, and a renderer that ignored the quality knob would return equal lengths and fail.
  - `[low]` `[reject]` A sibling left `received` after a dead task fails the submission keeps a pending slot in `documents_total` — the result is already terminal `failed` with the dead file named in `decision.reasons`, and reconciling it needs a new progress branch for a cosmetic count rather than a direct correction.
  - `[low]` `[reject]` `build_storage` sits outside the `processing_failed` try, so a failure there omits the filename — failure to construct the storage backend is a deployment-wide misconfiguration, not a per-file preparation error; the task error already carries the underlying message, and wrapping it would blame one file for a global fault.
  - `[low]` `[reject]` carried — `CONTINUATION_CONFIDENCE = 0.6` is a literal rather than a `Settings` value, so it sits below an operator floor raised above 0.6 — carried from the previous pass: default behaviour is sound and the continuation policy is revisited in stories 4–5.
  - `[low]` `[patch]` The mid-flight progress branch was untested, so dropping the term passed the whole suite — now observed by `test_progress_counts_a_sibling_that_is_still_waiting`.
  - `[low]` `[patch]` The rendering knobs were only asserted at their defaults, so handler literals passed — the page-atom test now runs at 120 DPI and a 96 px thumbnail cap, and `test_page_image_quality_setting_changes_the_stored_image` compares stored bytes at quality 20 and 95 through the handler.
  - `[low]` `[patch]` The prepare temp-file cleanup was never observed — `prepare_temp_snapshot()` now brackets both the successful drain and the retried render-failure path.

## Auto Run Result

**Summary:** Follow-up review pass on the completed story. No code behaviour changed except one classifier-reporting correction: an `unknown` verdict reached only through weak absolute evidence no longer reports the misleading top-two share (often `1.0`) as `document_type_confidence`; it reports `0.0`. The rest of this pass closes verification gaps and documentation drift: mid-flight progress counting, off-default rendering settings, prepare temp-file cleanup, the result-object contract, the segmentation section of the architecture blueprint, and the exact `document_hash` formula.

**Files changed:**
- `src/trade_extract/services/segmentation.py` — `unknown` from weak evidence reports confidence `0.0`; docstring names the raw-score gate.
- `src/trade_extract/config.py`, `.env.example` — document that the confidence floor gates both the top-two share and the raw weighted score.
- `docs/04-api.md` — one result object per segment; duplicates reported only through `duplicate_documents`.
- `docs/01-architecture.md` — §6.3 now describes the implemented embedded-text classifier, continuation inheritance and the later vision slice.
- `docs/06-document-schemas.md` — exact `document_hash` formula and rendering.
- `tests/unit/test_segmentation.py` — weak-signal `unknown` asserts confidence `0.0`.
- `tests/support.py` — `prepare_temp_snapshot()` helper.
- `tests/integration/test_pages_segmentation.py` — non-default render settings, quality-through-handler test, mid-flight progress test, prepare temp-cleanup assertions.
- `_bmad-output/implementation-artifacts/deferred-work.md` — orchestrator-authored ledger content carried unchanged and committed as found.
- `_bmad-output/specs/spec-trade-doc-extraction/stories/2-page-model-and-always-on-segmentation.md` — this story record.

**Review findings breakdown:** 22 findings — 3 medium, 15 low, 4 false. 9 findings patched in 8 patch entries (3 medium, 6 low; two findings shared one test fix), 0 deferred, 13 rejected (8 low by rule, 4 refuted, 1 carried from the previous pass). Rejected findings and their reasons are recorded row by row in the Review Triage Log above. No `high` finding; no spec-level (`bad_spec` or `intent_gap`) loopback.

**Follow-up review recommendation:** `false`. This is a follow-up pass and nothing `high` was patched, so the work has converged. Patched counts by verdict: medium 3, low 6, high 0.

**Verification performed:**
- `uv run pytest -q tests/unit/test_segmentation.py tests/unit/test_page_model.py tests/integration/test_pages_segmentation.py` — 51 passed.
- `uv run ruff check .` — all checks passed; `uv run ruff format --check .` — 96 files formatted.
- `uv run mypy src/trade_extract/domain src/trade_extract/services` — no issues in 14 source files.
- `uv run pytest -q` — 207 passed.
- `uv run pytest -q -m integration` — 99 passed, 108 deselected, against PostgreSQL 16.
- `uv run pytest -q tests/integration/test_migrations.py` — 1 passed; `alembic check` reported "No new upgrade operations detected."

**Residual risks:**
- Classification accuracy on real trade documents remains unmeasured; this pass did not add a labelled corpus (story 7 owns it).
- The file row lock and its transaction still span download, rasterisation, uploads and segmentation (deferred and owned by the orchestrator's ledger).
- Image containers still record `rotation_deg` as `0.0`; story 5 owns orientation policy.
- A submission failed by a dead task can leave an unprocessed sibling counted as pending in `documents_total`; the result is terminal `failed` regardless.


