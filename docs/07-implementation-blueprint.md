# 07 — Implementation Blueprint

This is the build plan, not the build. It fixes the code contracts that the documents in this folder
depend on, so the implementation phase does not have to re-litigate them.

## 1. Build order

The order follows the story list but front-loads the contracts that are expensive to reverse.

| Phase | Stories | Deliverable | Exit criterion |
|---|---|---|---|
| 0. Skeleton | — | Repo, config, logging, DB session, migrations, health, Docker Compose. | `docker compose up` serves `/v1/health` and migrates a clean database. |
| 1. Intake | 1 | Clients, API keys, jobs, submissions, files, upload validation, queue, `POST /v1/extract`, result polling. | A 202 is returned, a task is claimed, and a stub result is pollable. |
| 2. Pages | 2 | File → pages, rasterisation, text layer, always-on segmentation. | A mixed PDF yields non-overlapping segments with correct ranges. |
| 3. Schemas | 3 | Envelope, provenance, seven schemas plus proposed packing list. | Every schema generates JSON Schema and rejects a field without provenance. |
| 4. Readers | 4 | Provider adapter, text-layer and vision readers, validate-and-repair. | A digital invoice yields a validated field set with boxes. |
| 5. Conditioning | 5 | Orientation, deskew, crop, DPI, denoise, legibility score, unreadable outcome. | A photographed page is corrected or refused with a reason; no fabricated fields. |
| 6. OCR | 6 | PaddleOCR reader, PP-Structure, table reconstruction, routing policy. | A 200-row table returns all rows or names unresolved rows. |
| 7. Eval | 7 | Corpus intake, per-field metrics, review-rate measurement. | Baseline numbers exist per document type, with sample sizes stated. |
| 8. Invoice gate | 8–9 | Invoice extraction, deterministic checks, decision, review item. | A real invoice passes or fails with a named reason. |
| 9. Conflicts | 10–11 | Canonical keys, conflict detection, repeat-call accumulation. | Invoice + packing list disagree; one conflict names both sources. |
| 10. Review | 12–13 | Two-track review console, resolution records, trust records. | Extraction and conflict queues have separate counts; resolutions persist. |
| 11. Coverage | 14–15 | Remaining six types, all review-only. | All types extract and route correctly. |
| 12. Hardening | — | Retention, webhooks, exports, metrics, load test, security pass. | Retention alert fires on an induced failure; load target met. |

Nothing downstream is tuned before Phase 7 reports real numbers. The labelled corpus is a
prerequisite, not a later story.

## 2. Runtime choices fixed here

| Choice | Decision |
|---|---|
| Database access | SQLAlchemy 2.0 **sync** `Session` with psycopg 3. |
| API handlers | Sync `def` handlers where DB is touched; FastAPI runs them in a threadpool. |
| Worker | Sync process; one task at a time; OCR protected by a process-local semaphore. |
| HTTP client | `httpx.Client` for provider and webhooks in the worker. |
| Time | Injected `Clock` protocol; no `datetime.now()` in domain or services. |
| IDs | `domain.ids.new_id("doc_")`, `uuid4().hex`. |
| JSON | `domain.hashing.stable_json` for hashing and prompt prefixes. |
| Migrations | Alembic, forward-only, one-shot deployment job. |
| Packaging | `uv`, `pyproject.toml`, lock committed. |

Why sync: the workload is I/O-bound at the API edge and CPU/process-bound in the worker. A sync
session keeps transaction boundaries obvious, avoids an async SQLAlchemy upgrade path in the most
sensitive code, and lets the worker use the same repositories without a second dialect. FastAPI's
threadpool is sufficient for the expected request rate; if the API becomes thread-bound, the fix is
more stateless replicas before it is an async rewrite.

## 3. Configuration

`config.py` is environment-only. No secret has a production default.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    secret_key: str
    api_key_pepper: str

    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    storage_dir: Path = Path("./var/storage")
    s3_bucket: str = ""
    s3_endpoint_url: str = ""

    provider: Literal["mock", "deepseek"] = "mock"
    deepseek_api_key: str = ""
    deepseek_vision_model: str = "deepseek-flash"
    deepseek_text_model: str = "deepseek-v4-pro"

    max_documents_per_group: int = 20
    max_file_bytes: int = 26_214_400
    max_pages_per_file: int = 500
    max_pages_per_submission: int = 2_000

    document_retention_hours: int = 6
    job_max_attempts: int = 3
    webhook_max_attempts: int = 8
```

Validation rules live in `field_validator` methods: group limit 1–100, file limit ≥ 1 KiB, page
limits positive, retention > 0. `get_settings()` is `lru_cache`d and called only at process
boundaries.

The worker image installs `--extra worker --extra ocr`; the API image installs base dependencies
only. A test imports `trade_extract.api.app` in a clean environment and asserts that
`paddleocr`, `cv2`, `numpy` and `pypdfium2` are absent.

## 4. Database session and models

```python
# db/base.py
class Base(DeclarativeBase):
    pass

# db/session.py
def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_pre_ping=True,
        future=True,
    )

def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False, autoflush=False)
```

Every request and task gets one session. Transactions are short:

```python
with session.begin():
    job = jobs.upsert_for_submission(session, client_id, job_ref, policy_id)
    submission = submissions.create(session, job, ...)
    ...
# commit before storage I/O or model calls
```

Never hold a transaction open across an object-storage call, a provider call or image processing.
The pattern is: claim/read → commit or close → do the expensive work → open a new transaction →
write the result. Idempotent handlers make the gap safe.

## 5. Queue claim and runner

```python
# services/queue.py
CLAIM_SQL = text("""
WITH claimed AS (
    SELECT id
    FROM job_tasks
    WHERE status = 'queued'
      AND run_after <= now()
      AND kind = ANY(:kinds)
    ORDER BY priority DESC, run_after, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE job_tasks t
SET status = 'running',
    locked_by = :worker_id,
    locked_at = now(),
    started_at = COALESCE(t.started_at, now()),
    attempts = t.attempts + 1
FROM claimed c
WHERE t.id = c.id
RETURNING t.*;
""")

def claim_task(session: Session, worker_id: str, kinds: list[str]) -> TaskRow | None:
    row = session.execute(CLAIM_SQL, {"worker_id": worker_id, "kinds": kinds}).mappings().first()
    return TaskRow(**row) if row else None
```

```python
# workers/runner.py
def run_forever(worker_id: str, kinds: list[str]) -> None:
    while not stop_requested():
        with session_factory() as session, session.begin():
            task = claim_task(session, worker_id, kinds)
        if task is None:
            sleep(settings.worker_poll_seconds)
            continue
        try:
            result = HANDLERS[task.kind](task)
            with session_factory() as session, session.begin():
                mark_succeeded(session, task, result)
        except RetryableError as exc:
            with session_factory() as session, session.begin():
                mark_failed(session, task, exc, retry=True)
        except Exception as exc:
            with session_factory() as session, session.begin():
                mark_failed(session, task, exc, retry=task.attempts < task.max_attempts)
```

Rules:

- A handler is idempotent. Re-running `extract_segment` for an already extracted segment returns the
  existing document instead of writing a duplicate.
- A heartbeat updates `locked_at` every 30 seconds for long tasks. The stale-task reaper requeues
  `running` tasks older than the visibility timeout.
- Retry backoff is exponential with jitter: `min(300, 2 ** attempts) + random(0, 30)` seconds.
- `failed` with attempts exhausted becomes `dead`; a dead task is visible in the admin UI and can be
  replayed.
- Task payloads contain IDs only, never document text or image bytes.

## 6. Storage

```python
# services/storage.py
class ObjectStorage(Protocol):
    def put(self, key: str, stream: BinaryIO, content_type: str) -> None: ...
    def get(self, key: str) -> BinaryIO: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def presigned_get(self, key: str, expires_in: int) -> str: ...
```

Filesystem implementation:

- Root is `settings.storage_dir`, resolved once.
- Keys are normalized and rejected if they escape the root (`..`, absolute paths, symlinks).
- Writes go to a temporary file in the same directory, then `os.replace` for atomicity.
- `delete_prefix` walks the prefix and removes empty directories.
- No public URL; the review UI streams through an authenticated route.

S3 implementation:

- `boto3.client("s3")` with the configured endpoint, region and credentials.
- Server-side encryption enabled by default.
- `delete_prefix` lists and deletes in batches; it also deletes non-current versions when versioning
  is enabled, or the retention promise is false.
- `presigned_get` is used only for account exports, never for review media.

Key scheme:

```text
clients/{client_id}/jobs/{job_id}/files/{file_id}/source.{ext}
clients/{client_id}/jobs/{job_id}/files/{file_id}/pages/{page_index:04d}.png
clients/{client_id}/jobs/{job_id}/files/{file_id}/pages/{page_index:04d}.thumb.png
clients/{client_id}/jobs/{job_id}/files/{file_id}/crops/{page_index:04d}/{crop_id}.png
```

## 7. Provider adapter

```python
# providers/base.py
@dataclass(frozen=True)
class PageImage:
    page_index: int
    png_bytes: bytes
    width_px: int
    height_px: int

@dataclass(frozen=True)
class VisionRequest:
    document_type: str
    schema_version: str
    schema_json: dict[str, Any]
    pages: tuple[PageImage, ...]
    crops: tuple[PageImage, ...]
    ocr_text: str | None
    prompt_hash: str

@dataclass(frozen=True)
class ProviderResult:
    raw_json: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int

class ModelProvider(Protocol):
    name: str
    def vision_extract(self, request: VisionRequest) -> ProviderResult: ...
    def text_reason(self, request: TextRequest) -> ProviderResult: ...
```

DeepSeek adapter:

- `POST {base_url}/chat/completions`.
- Vision model `deepseek-flash`; text model `deepseek-v4-pro`.
- `response_format={"type": "json_object"}` for extraction.
- System prompt per document type, byte-stable, no timestamps, no random IDs, fixed field order.
- Images are sent in `user` messages only. Large files use the Files API.
- Retry on `429`, `5xx`, timeouts and connection errors with jitter; never retry a `400`.
- Every call writes `provider_calls`: model, prompt hash, tokens, latency, status, error code. No
  content, no prompt body.
- Prompt hash is recorded so cache-hit behaviour can be verified without storing the prompt.

Mock adapter:

- Deterministic fixtures keyed by `(document_type, source_hash)`.
- Returns candidates with plausible boxes for tests and local development.
- Can be told to return invalid JSON or a schema violation to exercise repair and failure paths.

## 8. Readers and routing

```python
class Reader(Protocol):
    name: str
    def read(self, page: PageInput, spec: SchemaSpec) -> list[FieldCandidate]: ...
```

`FieldCandidate` is a domain type:

```python
@dataclass(frozen=True)
class FieldCandidate:
    field_path: str
    section: str
    value: str
    value_type: ValueType
    raw_text: str
    page_index: int
    bbox: BBox
    reader: ReaderName
    model: str | None
    confidence: float | None
```

Routing policy is data:

```python
def route(page: PageFeatures, policy: RoutingPolicy) -> list[ReaderName]:
    if page.status == "unreadable":
        return []
    if page.text_layer_chars >= policy.digital_min_chars and page.text_quality >= policy.digital_min_quality:
        readers = ["text_layer"]
        if page.table_count > 0 and policy.paddle_available:
            readers.append("paddle")
        return readers
    if page.skew_deg <= policy.flat_max_skew and page.legibility >= policy.flat_min_legibility:
        return ["paddle", "vision"]
    return ["vision", "paddle"] if policy.paddle_available else ["vision"]
```

The policy is versioned with the extraction policy. The baseline rules are:

| Page | Readers |
|---|---|
| Digital, clean text layer | text layer alone; structural OCR and native vision crops for tables |
| Clean flat scan | PaddleOCR + vision |
| Photographed / skewed / stamped | vision + PaddleOCR secondary; dense regions as crops |
| Table-heavy | PaddleOCR PP-Structure + vision headers |
| Unreadable | none |

Dense regions are never read from a downscaled whole page. The table reader finds the cell/row
boxes, crops at native resolution, and sends those crops with page coordinates.

## 9. Field merge

```python
def merge_candidates(
    candidates: list[FieldCandidate],
    spec: SchemaSpec,
    policy: MergePolicy,
) -> list[MergedField]:
    merged: list[MergedField] = []
    for path, group in group_by(candidates, key=lambda c: c.field_path):
        normalized = {normalize(c.value, c.value_type): c for c in group}
        if len(normalized) == 1:
            agreement = Agreement.UNANIMOUS if len(group) > 1 else Agreement.SINGLE_READER
            selected = choose(group, policy)
        elif len(group) >= 3 and majority(normalized):
            agreement = Agreement.MAJORITY
            selected = choose_majority(group, normalized, policy)
        else:
            agreement = Agreement.DISPUTED
            selected = choose(group, policy)
        merged.append(MergedField(
            field_path=path,
            value=selected.value,
            value_type=selected.value_type,
            raw_text=selected.raw_text,
            provenance=selected.provenance,
            agreement=agreement,
            alternatives=group if agreement is Agreement.DISPUTED else [],
        ))
    return merged
```

Selection is a policy decision, not a reader privilege:

- Unanimous: use the candidate with the highest evidence quality; agreement is recorded.
- Majority: use a majority candidate; the minority is retained as an alternative.
- Disputed: select by the policy's tie-break (text layer on a high-quality digital page, otherwise
  the highest-quality vision candidate), mark `disputed`, retain every alternative.
- A disputed critical field is blocking for the invoice gate. It is still emitted, because the
  reviewer needs to see the chosen value and the alternatives.

`normalize` is type-aware: decimal parsing without float, date parsing, enum case folding, whitespace
collapsing. It never changes the emitted `value`.

## 10. Validation and repair

```python
def extract_with_repair(
    provider: ModelProvider,
    request: VisionRequest,
    spec: SchemaSpec,
) -> ExtractionResult:
    raw = provider.vision_extract(request)
    for attempt in range(3):
        try:
            model = spec.model.model_validate(raw.raw_json)
            validate_provenance(model)
            validate_table_completeness(model, spec)
            return ExtractionResult.ok(model, raw)
        except ValidationError as exc:
            if attempt == 2:
                return ExtractionResult.failed(exc, raw)
            request = request.with_repair(errors=exc.errors(), previous=raw.raw_json)
            raw = provider.vision_extract(request)
    raise AssertionError("unreachable")
```

The repair prompt contains only the schema errors and the previous JSON, not new document text. It is
capped at two attempts. A failure creates an extraction review item; the document is not marked
extracted.

## 11. Deterministic checks

```python
@dataclass(frozen=True)
class CheckResult:
    code: str
    tier: int
    status: Literal["pass", "fail", "warn", "skipped"]
    field_path: str | None
    expected: Any
    actual: Any
    severity: Literal["info", "warning", "blocking"]
    message: str


def run_invoice_checks(doc: CommercialInvoice) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(require_fields(doc, INVOICE_REQUIRED))
    results.append(check_line_extensions(doc))
    results.append(check_line_sum(doc))
    results.append(check_tax_identity(doc))
    results.append(check_payable_identity(doc))
    results.append(check_currency(doc))
    results.append(check_incoterm(doc))
    results.append(check_dates(doc))
    results.append(check_container_numbers(doc))
    results.append(check_hs_codes(doc))
    results.append(check_reader_agreement(doc))
    return [r for r in results if r is not None]
```

Rules:

- Parse stated values with `Decimal`, never `float`.
- Treat a missing optional field as `skipped`, not `fail`.
- A failed blocking check produces a human-readable message naming the field, expected and actual.
- Computed values are local variables for comparison; they are never written to `field_values` or
  the output snapshot.
- The check set is versioned with the document schema. `checks.check_version` records it.

Invoice check codes (Tier 1):

| Code | Check |
|---|---|
| `INV-001` | Required fields present. |
| `INV-002` | Line extension amount = quantity × unit price (currency rounding tolerance). |
| `INV-003` | Sum of line extension amounts = stated line extension total. |
| `INV-004` | Tax exclusive + tax amount = tax inclusive. |
| `INV-005` | Tax inclusive + charges − allowances − prepaid = payable. |
| `INV-006` | Currency is ISO 4217. |
| `INV-007` | Incoterm is a valid Incoterms 2020 code. |
| `INV-008` | Issue date ≤ due date; no obviously impossible date. |
| `INV-009` | Quantity > 0, unit price ≥ 0, amounts ≥ 0 where stated. |
| `INV-010` | Container number passes ISO 6346 check digit. |
| `INV-011` | HS code is 6–10 digits with valid structure. |
| `INV-012` | Critical fields are not reader-disputed. |
| `INV-013` | Tax breakdown sums to the stated tax total. |
| `INV-014` | Amount in words agrees with the numeric payable, when both are stated. |

`INV-014` is a warning, not blocking, because word forms are layout-sensitive. The rest are blocking
for an invoice pass.

## 12. Conflicts

```python
def detect_conflicts(
    job: Job,
    documents: list[DocumentRecord],
    canonical: CanonicalMap,
) -> list[ConflictDraft]:
    values: dict[str, list[SourceValue]] = defaultdict(list)
    for doc in documents:
        for path, field in doc.fields.items():
            key = canonical.key_for(doc.document_type, path)
            if key is None:
                continue
            values[key].append(SourceValue(
                document_id=doc.id,
                document_type=doc.document_type,
                field_value_id=field.id,
                stated=field.value,
                normalized=normalize_for_key(key, field.value),
                provenance=field.provenance,
            ))

    conflicts: list[ConflictDraft] = []
    for key, sources in values.items():
        distinct = {s.normalized for s in sources}
        if len(distinct) <= 1:
            continue
        conflicts.append(ConflictDraft(
            canonical_key=key,
            field_label=canonical.label(key),
            fingerprint=fingerprint_conflict(job.id, key, sources),
            sources=sources,
        ))
    return conflicts
```

Upsert logic:

- Look up `(job_id, canonical_key, fingerprint)`.
- If an open conflict exists, add any new sources and update `last_seen_at`.
- If a resolved conflict exists and the same disagreement reappears, create a new open conflict; do
  not reopen the old resolution.
- Recompute on every submission, inside the same transaction that writes the new documents.
- Never delete sources, never edit `stated_value`, never set a winner.

## 13. Decisions

```python
def decide_document(
    doc: DocumentRecord,
    checks: list[CheckResult],
    open_conflicts: list[ConflictRecord],
    policy: Policy,
) -> Decision:
    reasons: list[str] = []

    if doc.status == "unreadable":
        return Decision("unreadable", "none", ["unreadable"], policy)
    if doc.status == "failed":
        return Decision("failed", "none", ["extraction_failed"], policy)

    blocking = [c for c in checks if c.severity == "blocking" and c.status == "fail"]
    reasons.extend(f"check_failed:{c.code}" for c in blocking)

    if doc.document_type != "commercial_invoice":
        reasons.append("review_depth:full")
    elif not policy.invoice_pass_enabled:
        reasons.append("policy:invoice_pass_disabled")
    elif blocking or any(c.status == "warn" for c in checks if c.code == "INV-012"):
        pass  # already recorded
    elif open_conflicts:
        reasons.extend(f"conflict_open:{c.canonical_key}" for c in open_conflicts)
    elif doc.has_unreadable_page:
        reasons.append("unreadable_page")
    else:
        return Decision("pass", "none", [], policy)

    route = "conflict" if open_conflicts else "extraction"
    return Decision("review", route, sorted(set(reasons)), policy)
```

The job decision is the union: any document `failed` → job `failed`; any open conflict → job
`review` on the conflict route; any open extraction item → job `review` on the extraction route;
otherwise `pass` if every document passed, else `review`.

Every decision writes `policy_id` and `policy_version`. A policy activation never rewrites history.

## 14. Review service

```python
def resolve_extraction(
    session: Session,
    item_id: str,
    reviewer: Reviewer,
    *,
    field_path: str | None,
    outcome: ResolutionOutcome,
    corrected_value: str | None,
    reason: str,
) -> ReviewResolution:
    item = reviews.get_for_update(session, item_id)
    if item.track != "extraction" or item.status not in ("open", "in_progress"):
        raise ConflictError("review_item_not_open")

    value = fields.get_for_update(session, item.document_id, field_path) if field_path else None
    resolution = reviews.create_resolution(
        session,
        item=item,
        reviewer=reviewer,
        field_path=field_path,
        prior_value=value.value if value else None,
        corrected_value=corrected_value,
        outcome=outcome,
        reason=reason,
    )

    if outcome is ResolutionOutcome.CORRECTED:
        fields.apply_human_correction(session, value, corrected_value, reviewer, reason)
        trust.demote(session, item.document, reason="human_correction")

    reviews.close(session, item)
    jobs.recount_open_items(session, item.job_id)
    audit.record(session, reviewer, "review.resolve", item, {"outcome": outcome.value})
    return resolution
```

The original `documents.extraction` snapshot is immutable. A correction writes a new `field_values`
row state and a resolution record; the result view uses the latest resolved value and keeps the prior
value in the resolution history. A conflict resolution writes only a resolution; it never touches
`field_values`.

## 15. Retention sweeper

```python
def sweep(clock: Clock, session_factory: sessionmaker, storage: ObjectStorage, settings: Settings) -> None:
    with session_factory() as session, session.begin():
        sweep_row = retention.start_sweep(session)

    due = retention.list_due_jobs(session_factory, clock.now(), settings.document_retention_hours)
    deleted = 0
    failures = 0
    for job in due:
        try:
            keys = retention.object_keys_for_job(job)
            deleted += storage.delete_prefix(f"clients/{job.client_id}/jobs/{job.id}/")
            with session_factory() as session, session.begin():
                retention.mark_purged(session, job.id)
        except Exception as exc:
            failures += 1
            log.error("retention_failure", extra={"job_id": job.id, "error": type(exc).__name__})
    with session_factory() as session, session.begin():
        retention.finish_sweep(session, sweep_row.id, deleted, failures)
```

The query is `SELECT … FOR UPDATE SKIP LOCKED` over jobs whose `terminal_at` is older than the
window and whose open review/conflict counts are zero. The sweeper is idempotent: an already purged
object is a success, not a failure.

Metrics:

- `retention_due_jobs`
- `retention_objects_deleted_total`
- `retention_failures_total`
- `retention_oldest_overdue_seconds`

Alert when `retention_oldest_overdue_seconds > 1800` or `retention_failures_total` increases.

## 16. Webhooks

```python
def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"v1={digest}"
```

Delivery:

```python
def deliver(delivery: WebhookDelivery, client: Client) -> DeliveryResult:
    body = stable_json_bytes(delivery.payload)
    timestamp = int(clock.now().timestamp())
    headers = {
        "Content-Type": "application/json",
        "X-TradeExtract-Event": delivery.event_type,
        "X-TradeExtract-Delivery": delivery.id,
        "X-TradeExtract-Timestamp": str(timestamp),
        "X-TradeExtract-Signature": sign(decrypt(client.webhook_secret), timestamp, body),
    }
    response = http.post(
        delivery.url,
        content=body,
        headers=headers,
        timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
        follow_redirects=False,
    )
    return DeliveryResult(status_code=response.status_code, body=response.text[:512])
```

SSRF guard resolves the host and rejects private, loopback, link-local and metadata ranges before
the request. The outbox row is created in the same transaction as the result completion; a separate
maintenance loop claims due deliveries with `SKIP LOCKED`.

## 17. API route skeleton

```python
@router.post(
    "/extract",
    status_code=202,
    response_model=ExtractAccepted,
    responses={413: ErrorResponse, 422: ErrorResponse, 429: ErrorResponse},
)
def extract(
    request: Request,
    job_ref: Annotated[str | None, Form(max_length=128)] = None,
    documents: Annotated[list[UploadFile], File()] = ...,
    webhook_url: Annotated[str | None, Form(max_length=2048)] = None,
    client: Client = Depends(require_scope("extract:write")),
    session: Session = Depends(get_session),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExtractAccepted:
    validated = ingest.validate_submission(
        documents=documents,
        job_ref=job_ref,
        webhook_url=webhook_url,
        settings=settings,
    )
    accepted = ingest.accept(
        session=session,
        client=client,
        validated=validated,
        idempotency_key=idempotency_key,
        storage=storage,
        clock=clock,
    )
    return ExtractAccepted.from_record(accepted)
```

The route does not parse PDFs, run OCR or evaluate checks. It delegates. `ingest.accept` writes the
job/submission/files and enqueues `prepare_file` tasks in one transaction; object storage writes
happen after the file is validated and before the transaction commits, with a compensating delete
on commit failure.

## 18. Review UI route skeleton

```python
@router.get("/documents/{document_id}", response_class=HTMLResponse)
def document_workspace(
    request: Request,
    document_id: str,
    reviewer: Reviewer = Depends(require_reviewer),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    doc = documents.get_authorized(session, document_id, reviewer)
    context = {
        "document": doc,
        "sections": fields.group_by_section(doc),
        "checks": checks.list_for_document(session, doc.id),
        "csrf_token": csrf.token(request),
        "reviewer": reviewer,
    }
    return templates.TemplateResponse(request, "documents/workspace.html", context)
```

HTMX fragments return partial templates. Every mutation validates CSRF, checks the session epoch,
writes a resolution, and returns a fragment plus an `HX-Trigger` toast event.

## 19. Testing strategy

| Layer | What | Tooling |
|---|---|---|
| Unit | IDs, hashing, provenance, path grammar, normalization, checks, conflicts, decisions. | pytest, no DB, no network. |
| Integration | Upload → queue → pipeline → result with mock provider; Postgres; filesystem storage. | pytest, httpx, testcontainers or Compose Postgres. |
| Contract | OpenAPI snapshot; error envelope; document schema snapshots; webhook signature. | pytest + `openapi.yaml`. |
| Golden | Fixture PDFs → expected fields/provenance; table completeness. | JSON golden files; update only with review. |
| Eval | Labelled corpus; per-field precision/recall; review rate; conflict precision/recall. | `tests/eval/run_eval.py`. |
| Security | Auth, IDOR, scope, SSRF, magic bytes, CSRF, session fixation, rate limits. | pytest + targeted payloads. |
| Accessibility | Keyboard path, focus order, contrast, axe scan. | Playwright optional; manual checklist mandatory. |
| Load | 20-file group, 500 pages, queue throughput, DB pool. | Locust or k6, not part of unit CI. |

Rules:

- Tests inject a frozen `Clock`; no `sleep` for retention or webhook windows.
- The mock provider can return invalid JSON, a schema violation, a timeout or a rate limit on
  demand.
- Integration tests assert query counts for the result and review routes.
- No test asserts on model prose. It asserts on schema, provenance and deterministic outcomes.
- The eval corpus distinguishes real and synthetic documents; scores are never blended.

## 20. Docker

`Dockerfile.api`:

```dockerfile
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
CMD ["uv", "run", "uvicorn", "trade_extract.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

`Dockerfile.worker` uses the same base but installs `--extra conditioning --extra ocr --extra s3`.
It is the image with OpenCV and pypdfium2; PaddleOCR is installed only in the worker. Build it separately so a change to OCR
does not rebuild the API.

`docker-compose.yml` (abridged):

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: trade
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: trade_extract
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trade -d trade_extract"]
      interval: 5s
      timeout: 5s
      retries: 10

  migrate:
    build: { context: ., dockerfile: Dockerfile.api }
    command: ["uv", "run", "alembic", "upgrade", "head"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }

  api:
    build: { context: ., dockerfile: Dockerfile.api }
    env_file: .env
    ports: ["8000:8000"]
    depends_on:
      postgres: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/v1/health')"]
      interval: 10s
      timeout: 5s
      retries: 5

  worker:
    build: { context: ., dockerfile: Dockerfile.worker }
    command: ["uv", "run", "python", "-m", "trade_extract.workers"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }
    deploy:
      resources:
        limits: { memory: 4g, cpus: "2" }

  maintenance:
    build: { context: ., dockerfile: Dockerfile.worker }
    command: ["uv", "run", "python", "-m", "trade_extract.workers.maintenance"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }

volumes:
  pgdata:
```

Rules:

- `api` never installs OCR extras.
- Development uses the same images as production; only env and secrets differ.
- CPU is the default. GPU is an opt-in Compose profile, never required to boot.
- `docker compose down` does not delete `pgdata` by default.
- No secrets in the Compose file; use `.env` or a secret manager.

## 21. CI

```yaml
# .github/workflows/ci.yml (abridged)
jobs:
  quality:
    steps:
      - uses: actions/checkout@v4
      - run: uv sync --frozen --extra dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy src/trade_extract/domain src/trade_extract/schemas src/trade_extract/services
      - run: uv run pytest -q --cov=src/trade_extract --cov-fail-under=80

  integration:
    services:
      postgres:
        image: postgres:16
        env: { POSTGRES_USER: trade, POSTGRES_PASSWORD: trade, POSTGRES_DB: trade_extract }
        ports: ["5432:5432"]
    steps:
      - run: uv run alembic upgrade head
      - run: uv run pytest tests/integration tests/contract -q

  images:
    steps:
      - run: docker build -f Dockerfile.api -t trade-extract-api .
      - run: docker build -f Dockerfile.worker -t trade-extract-worker .
      - run: docker compose up -d --wait
      - run: ./scripts/smoke.sh
```

CI also:

- Runs `alembic check` for model/DDL drift.
- Diffs generated OpenAPI against `docs/openapi.yaml`.
- Asserts the API image does not import OCR libraries.
- Runs a retention test with a frozen clock and an induced storage failure; the alert must fire.

## 22. Eval harness

`scripts/eval.py`:

1. Loads a corpus directory of complete jobs, not loose samples.
2. Runs the real pipeline with the mock or live provider into a scratch database.
3. Compares each extracted field to the labelled truth with type-aware equality.
4. Reports, per document type and per field:
   - exact match rate,
   - precision and recall where absence matters,
   - provenance box hit rate (does the box enclose the labelled text?),
   - table row completeness,
   - unreadable rate,
   - review rate and pass rate,
   - conflict precision and recall on jobs with known disagreements.
5. Separates real from synthetic documents. Never blends scores.
6. States sample size and confidence interval; refuses to rank a type with fewer than 20 samples.
7. Writes a JSON report and a Markdown summary; both are artifacts, not committed secrets.

The harness is the only place that decides whether a model or prompt is better. Model choice is
provisional until the harness says otherwise.

## 23. Definition of done for v1

- All seven named types plus the packing-list decision are implemented and documented.
- Every emitted leaf field has provenance; a test fails on any missing box.
- The invoice gate passes a correct invoice and fails a transposed total with a named reason.
- A conflict is reported with both sources and never auto-resolved.
- Repeat submissions under one `job_ref` reconcile into one job.
- The two review queues are separate, and every resolution is a labelled record.
- Trust records exist, start at full review, and demote on correction.
- Files purge six hours after terminal; extraction records remain; the sweeper alerts on failure.
- `docker compose up` works from a clean clone with Docker only.
- CI is green; the eval report exists; the retention and webhook failure tests pass.



## Implemented conditioning contract (story 5)

`domain.conditioning` contains immutable image-free results, measurements and projective geometry.
`services.conditioning.condition_page(PageRendering, Settings)` lazily imports OpenCV, numpy and
Pillow in the worker, bounds decode and analysis, and returns JPEG image/thumbnail derivatives.
The worker image installs the `conditioning` extra without PaddleOCR. The API import contract also
imports this service to ensure merely importing it never imports heavy libraries.

Preparation calls conditioning for every page before storage. Existing page columns hold status,
version, score, conditioning JSON, reason and conditioned timestamp; the page/segment/document
replacement transaction and retry shortcut remain unchanged. Segmentation always uses original
embedded text, including unreadable pages. Source bytes are never overwritten.

The effective `CONDITIONING_*` quality/budget policy and output DPI, JPEG quality and thumbnail
settings are stored per page. `image-quality-v1` measures
99.9 minus 0.1 percentile contrast, Laplacian variance, fraction of pixels darker than 200, retained document short
edge, and line skew. Score = min(1, contrast/min_contrast, blur/max(1,min_blur),
retained_document_short_edge/min_short_edge); score is diagnostic, not a gate. Individual refusal reasons
are preserved separately. No OCR confidence or model score exists. See architecture's story 5
section for defaults, supported orientation evidence and conservative perspective limits.

Homographies act on pixel-edge coordinates of rendered source and conditioned images. Raster
warps shift by half a pixel to apply those same transforms to pixel centres. Character boxes map
all four corners and remain normalized to conditioned dimensions; no character boxes are invented.
Inverse point/polygon maps remain exact within floating-point tolerance; an enclosing axis-aligned
box is inherently lossy under perspective. Synthetic tests independently project landmarks and
assert forward alignment within 3 px and inverse point agreement within 0.001 px.

`PageInput` carries conditioning status/version/reasons and transform; `vision_ready` requires a
current-version conditioned result. Vision filters each request, including repair evidence. Extraction
excludes unreadable pages from every reader, reports page-specific reasons and never promotes a
mixed unreadable segment to an extracted document. Lifecycle activation remains deferred.


Conditioning follow-up: TIFF orientation is consumed by Pillow during decode and is not applied a
second time. Perspective sampling uses a conservative bound on the homography Jacobian over the
retained sheet; output dimensions are reduced until local magnification respects
CONDITIONING_MAX_ENLARGEMENT. Rotation canvas padding is excluded from that sampling bound.
Retained sheet dimensions and final derivative dimensions both constrain the detail floor, so
photo surrounds and DPI metadata cannot rescue or create resolution. Dominant vertical line
evidence is compared against horizontal line strength; conflicting significant skew is an explicit
unresolved-skew refusal. Supplied homographies must be finite, nonsingular, mutually inverse and
free of an image-crossing projective horizon, and conditioned dimensions must match image metadata
before direct vision dispatch or repair. Mixed-segment exits retain page refusal reasons alongside
provider or validation diagnostics, including no-evidence outcomes.


## Story 6 implementation: routing and independent tables

`services.extraction.extract` remains a reusable service; worker lifecycle activation is story 8.
`PageFeatures` carries caller-owned positive evidence. Good aligned text plus an explicit absence
of dense tables selects text alone. Explicit clean flat scans select OCR and vision. Photographs,
skew, stamps, handwriting and unknown visual evidence select vision as primary; marked pages use
OCR only for supporting structure. Unreadable pages reach no reader. Route reasons, configured
reader availability and effective version/thresholds accompany every service outcome. Defaults are
provisional pending story 7. A missing feature is unknown, never evidence of an unmarked scan.

`PaddleReader` lazily initializes serialized, process-cached mobile OCR and table engines. Base OCR
uses `PP-OCRv5_mobile_det` and `PP-OCRv5_mobile_rec`; `TableRecognitionPipelineV2` is constructed
only for an initial or late structure requirement and consumes cached overall OCR with layout and
duplicate OCR disabled. It returns
validated OCR spans separately from table-structure status, preserving usable scalar evidence when
structure fails. Cached evidence must match page/version and budgets. Pixels, spans, regions, rows,
cells and HTML bytes are bounded; malformed geometry and every nonfinite polygon vertex are
rejected. OCR confidence remains diagnostic. Every emitted Paddle field must match an observed
text span and its exact normalized conditioned-page rectangle; all semantic reasoning stays behind
the provider adapter and schema/repair loop. Explicit reader lists never initialize excluded OCR.

Explicit `thead`/`th` header rows are excluded from data inventory. Merged cells, overlapping grids,
ambiguous rows and unknown semantic correspondence prevent unsupported completeness. Semantic
correspondence can be established from validated provider row citations contained in physical rows;
a bounded second pass then uses the resulting global row inventory and native crops. Anonymous or
unrelated grids are not silently concatenated. Unlabelled headers cannot be guessed as certain.
Complementary readers are combined before segment-wide count/geometry reconciliation. Every
independent row must be emitted or named in `unresolved_rows`; shorter provider counts fail. An
explicit unresolved row remains visible in TableMeta and does not mean complete extraction.

`services.tables.native_crops` decodes each page once, uses outward integer pixel edges and never
upscales. Crop-local boxes map by offset and scale to normalized conditioned-page boxes; the saved
conditioning inverse maps landmarks to source pixels. Vision batches attach one original page for
scalar/header context plus bounded row crops. Each batch carries image positions, crop rectangles,
page size and global row indices only for attached images. Table citations must cite those rows.
Every batch envelope is schema/identity validated before merge; conflicting fields/counts fail.
Whole-page images are never the only evidence used to accept dense row extraction. Crop/pixel and
provider batch limits return explicit failures. No model calls occur in preparation/conditioning.

Worker dependencies pin `paddleocr[doc-parser]==3.3.2` and `paddlepaddle==3.2.2`; the API installs
neither. The worker supplies libgomp/OpenGL/GLib runtime libraries and writable model cache paths
under `/home/appuser/.paddlex` and `.paddleocr`. Provision model weights using the worker user and
persist these caches, or allow approved first-use downloads. Import-only verification is distinct
from model initialization and inference accuracy; injected backend tests do not establish either.
The adapter follows the official PPStructureV3 `overall_ocr_res` and `table_res_list` contract:
https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/PP-StructureV3.html .

## Stories 7-10 implementation

The invoice worker lifecycle, INV-001 through INV-014, validated gate policy,
canonical job conflicts and retained review items are implemented. Check identities
include concrete field paths so repeated line checks remain independently auditable.
Decision JSON records effective policy/check versions, diagnostics and routing
snapshots. An open conflict always blocks job pass and reports every source, including
sources that agree with one another. Existing sources and resolved conflicts are not
rewritten. A later occurrence after resolution creates a new open conflict.

The policy defaults to `invoice_pass_enabled=false`, `review_depth=full` and
`arithmetic_tolerance="0.01"`. Only an explicitly enabled policy with review depth
`checks`, all fourteen checks accounted for, and no blocking outcomes, unreadable
pages or open conflicts may pass. Confidence has no gate input. Unsupported amount
word forms warn; malformed arithmetic operands fail. No duty, levies or CIF arithmetic
is performed. Noninvoice extraction, review console, trust promotion and repeat-call
accumulation features remain outside this implementation.

The evaluator accepts validated complete-job manifests and invokes an injected pipeline,
then reports origins independently with denominators and Wilson intervals. The authored
synthetic adapter exercises the actual extraction/check/decision/conflict services;
PostgreSQL lifecycle tests exercise persistence and outbox parity. Synthetic results do
not establish model accuracy or justify threshold changes. See [evaluation.md](evaluation.md)
for CLI, corpus intake and scratch-database adapter instructions. Real evaluation and
calibration remain pending a labelled real corpus.

## Stories 11–15 runtime completion

Job accumulation reads, authenticated two-track review, retained correction overlays, trust
observations and extraction of the six remaining registered types are implemented. The automatic
gate evidence remains immutable; human resolution is represented by retained labels, resolved
status and independent queue counts. New trust identities are full-review, correction demotion
is scoped to the exact identity, and v1 has no promotion path. Source expiry cannot delete the
retained evidence used by later submissions. See docs/03–06 for the runtime boundaries and the
explicit decision to retain existing local schema paths.

## Local-first extraction budget

Before semantic inference, construct stable evidence lines and run conservative label/geometry
rules. Remove verified local scalar paths from the provider JSON Schema, send the compact evidence
index once, and repair only the failed unresolved stage. Enforce the configured evidence-character,
estimated-input-token, output-token, call-count and cost safety limits before dispatch. Persist
actual usage in the existing provider ledger. The release gate is a labelled-corpus average at or
below USD 0.04/page, at least 95% exact critical-scalar accuracy, 90% table-cell accuracy and p95
below 90 seconds/page.
