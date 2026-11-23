# Production release

Doc Hover serves the product page at `/`, customer accounts at `/workspace`,
staff at `/review`, and integrations at `/v1`. The customer workspace is scoped by
the account's client association. Customer API keys are scoped to the issuing client.
Staff reviewers and administrators are platform operators with cross-client duties.
Never issue a staff account to a customer.

## Deploy to staging

1. Build `Dockerfile.api` and `Dockerfile.worker` from one commit and push both
   images to the private registry. Set `API_IMAGE` and `WORKER_IMAGE` to their immutable
   tags or digests. Keep the previous pair available for rollback.
2. Provision a private PostgreSQL database with encrypted backups and point-in-time
   recovery, plus a private S3 bucket. Give the app only the bucket and object
   permissions it needs. Maintenance must be able to list and remove object versions
   and delete markers. Enable bucket versioning, encryption and a lifecycle rule for
   abandoned multipart uploads. Verify source upload, read, version deletion and
   access policy on the selected object store.
3. Inject secrets from the deployment environment. Provide `DATABASE_URL`,
   `SECRET_KEY`, `API_KEY_PEPPER`, `BASE_URL` (public HTTPS), `S3_BUCKET`,
   S3 region and credentials or role, and Resend or SMTP settings. Configure the worker
   with the chosen production extraction provider, model and credentials. Keep
   `PRODUCTION_AUTO_PASS=false` for the pilot and staff review every extraction
   before using the data downstream. A completed submission can carry review items.
4. Run `docker compose --env-file /path/to/release.env -f compose.production.yml up -d --wait`.
   The one-shot migration service applies Alembic and seeds the policy. Rehearse schema
   upgrades on a recent restored copy before a production release. The API returns 503
   from `/v1/health/ready` when the database revision does not match the application.
   Keep production and staging secrets, databases, buckets, API keys and inbound email
   addresses separate.
5. Route TLS traffic through the reverse proxy to the loopback API port. Configure a
   body limit matching `MAX_REQUEST_BYTES`, rate limits for upload and sign-in, and
   sanitized access logs that omit reset-token query strings. Trust forwarded headers
   only from the configured proxy. Put internal staff routes behind an identity-aware
   proxy or restricted network. The shared login, password reset, logout and static
   assets under `/review` must remain accessible to customers. Check both staff and
   customer sign-in through the real proxy before the pilot.
6. Start with ten worker processes. The default container limits allow up to 2 CPUs and
   4 GiB per worker. Size the host and PostgreSQL connection budget for all processes.
   Prime OCR model caches and run a scanned-document smoke job before the load run.

From the API container, provision client logins with
`python scripts/provision_reviewer.py USERNAME --role client --client-slug SLUG --email ADDRESS`.
Create clients and API keys in the staff console first. Test that suspension disables both
key and browser access. Treat a job awaiting review as retained evidence. The maintenance
worker removes source objects, page images and raw page text after a job has been
terminal for six hours by default. Structured extraction and audit records remain.

## Release checks

- Run the Python tests against disposable PostgreSQL and the JavaScript review tests.
  Use two test clients to check job lists and direct IDs, result reads, upload identity,
  staff routes and document media.
- Run three distinct workloads on the release host: digital PDFs, scanned PDFs and a mixed
  batch. Use at least ten representative extracts in each, including multi-page and
  table-heavy documents. For each workload repeat the ten-concurrent run after a restart
  and once warm. The benchmark fails if any input fails, has no extracted fields, or takes
  120 seconds or longer from upload to an extract available through the results API.
  This times extraction and queue wait. Human review time is measured separately.

  ```bash
  python scripts/benchmark_extraction.py --url "$STAGING_URL" --cohort mixed --concurrency 10 fixtures/mixed/*.pdf
  ```

  Set `EXTRACTION_BENCHMARK_API_KEY` to a disposable staging client's API key.
  Track total completion rate, p95, tail failures, queue age and provider rate limits.
   Sample `active_extraction_workers` and `stale_extraction_tasks` from
   `scripts/ops_status.py` during the burst to verify how many workers are draining
   the queue. For the scan run, compare `classification_ocr_page` and `page_ocr`
   durations with provider attempt timestamps to isolate OCR and inference delays.

- Score the same release on a separate labelled holdout corpus using the [evaluation
  format](evaluation.md). Agree on the target by document type and critical field before
  the trial. Suggested pilot targets are 95% correct document classification and 98%
  precision and recall for critical fields, with no unreviewed critical errors. Use the
  actual denominators, and measure line item completeness and provenance as well.
  Synthetic fixtures validate behavior and cannot establish real extraction accuracy.

  For an authorized staging corpus set `EXTRACTION_EVALUATION_URL`,
  `EXTRACTION_EVALUATION_API_KEY`, and `ALLOW_REAL_EVALUATION=1`, then run:

  ```bash
  python -m scripts.eval /path/to/labels --pipeline scripts.eval_staging:run_job --output /path/to/report
  ```

  Label manifests with multiple extracted documents in one file must include zero-based
  `page_start` and `page_end` for each such document. A segmentation mismatch stops the
  evaluation so missed or extra segments cannot disappear from the report.

- Before cutover, verify password delivery, webhook signing and retries, failed-job recovery,
  object expiry, and backup restore in staging. Monitor API error rate, queue age, dead
  tasks, provider errors and spend, review backlog, and retention errors. Assign an alert
  owner and test delivery. Restore into isolated PostgreSQL and object storage and serve
  a known test result before marking recovery verified. Use `python scripts/ops_status.py`
  inside the API container to collect content-free queue, provider, webhook and retention metrics.
- To roll back the application, revert the image references to the previous compatible
  release and recreate the services. Roll schema forward if production data depends on
  the new schema. Keep the database backup and the staging restore record for the cutover.
