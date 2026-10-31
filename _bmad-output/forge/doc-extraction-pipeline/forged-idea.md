# Forged idea — Kenyan trade-document extraction API

**What it is:** An API that accepts a caller-grouped set of trade documents (1..n), splits them into per-document segments, extracts the **stated** values with provenance, runs deterministic validation, and returns per-document JSON plus a conflict report. ERP-agnostic. Cloud-first. Seven document types: proforma invoice, commercial invoice, bill of lading, delivery order, port pass, custom entry, verification memo.

## Locked

1. **Cloud first.** On-prem is designed for but not built. A provider adapter sits behind every model call so the inference stack is swappable. No on-prem work until a signed deal requires it.
2. **Market is Kenya.** Target systems: KRA **iCMS** (customs declaration), **KenTrade TradeNet** (single window, IDF), **KPA / KWATOS** (port pass, delivery order).
3. **Contract: group in, per-document JSON out, plus a conflict report.** The caller asserts the grouping (`job_ref` / B/L number). The platform **never infers or matches relations**. A single document is just a group of one.
4. **Adopt standard spines, don't invent a schema.** UBL 2.1 / Peppol BIS Billing 3.0 for the invoice family; WCO Data Model for custom entry. Thin proprietary extension block for delivery order, port pass, verification memo. National code is a *field*, never baked into field names.
5. **Foundational assumption:** the field schema is invariant across document layouts; only the layout→field mapping varies. This is why schema-first extraction beats template-based IDP.
6. **Data model:** File (container) → Page (atom) → Segment (`page_range`, doctype, confidence) → Document → Extraction (fields + per-field provenance bbox). Provenance is mandatory on every emitted field.
7. **Segmenter always runs**, never conditionally. It is cheap relative to extraction and is the safety net.
8. **Values are as-stated, never computed.** The document is authoritative for totals, values, and duty. Arithmetic runs only as a test oracle.
9. **Two separate dataflows:** extraction writes provenance-backed stated values to the client; validation computes checks solely to decide *post vs review*. Nothing computed is ever emitted as data.
10. **No duty or CIF computation.** Emit incoterm + goods value + freight + insurance as separate stated fields with sources. No derived `cif_value` field.
11. **Gate:** auto-post is decided by deterministic checks (check digits incl. ISO 6346, arithmetic identities, structural/enum validity, cross-document equality, temporal ordering, referential integrity) plus falsifiable signals (text-layer vs vision reader disagreement, per-client historical distributions, expected-field presence). **Model confidence ranks the queue; it never gates.**
12. **Trust ramp** per (client, supplier, doctype, template fingerprint): UNSEEN/OBSERVED = 100% review; TRUSTED = permanent random sample at a **client-visible** rate. Any human correction demotes; a new template resets to UNSEEN. The permanent sample doubles as the continuous eval harness.
13. **Two review queues, not one:** *extraction review* (we misread — our fix) vs *conflict review* (the papers genuinely disagree — the client's business call). Different owners, different SLAs, different metrics.
14. **Two reader paths** per document: embedded text layer and vision. Disagreement on a number is a primary Tier-2 signal. Works best on digital PDFs.
15. **Coverage:** all seven types covered by end of project. Coverage ≠ automation — automation follows volume × determinism: invoice → proforma → custom entry → delivery order → port pass → verification memo → B/L last. Take a thin vertical slice across all seven in the first third of the project so the B/L problem surfaces early.
16. **v1 refuses multi-shipment containers loudly** ("contains N shipments, split and resubmit"). Everything else — multi-doctype files, multi-file groups, duplicates, large scans — is absorbed by the data model.
17. **Email alerts, platform resolution.** Notification may go by email; resolution only inside the authenticated platform, never by replying with values.
18. **Idempotency:** deterministic document key (content hash + job ref) on any downstream ERP call, or duplicates and retries create duplicate documents in the client ledger.

## Rejected, and why

- **Separate file-level classifier.** Misroutes send a doc to the wrong extraction prompt and produce confidently wrong output. → Page-level segmenter instead.
- **Batch of 5 documents as throughput control.** Latency multiplier, not throughput. → Queue with a concurrency limit sized to rate limits.
- **ICM as the runtime orchestrator.** ICM is a human-checkpointed content workflow; this is high-throughput transactional extraction. → Use ICM-style file layout for prompt/schema artifacts only; runtime is a deterministic worker.
- **Per-client ERP mapping as prompts or per-client code.** Unmaintainable past a handful of clients. → Deterministic config, and deferred to a separate on-prem platform.
- **Model self-reported confidence as the gate.** Uncalibrated, and drifts silently whenever the model or prompt changes.
- **Merged per-job JSON.** The ERP exposes seven document-creation APIs; a merged blob is unauditable and the client has to unpick it.
- **B/L number as the single primary key.** Doesn't exist yet when the proforma is issued; not unique for consolidated LCL cargo (one master B/L, many house B/Ls). → Caller-supplied grouping.
- **Job matching / reference-graph inference across emails.** Deleted by making grouping an input rather than an inference.
- **PaddleOCR in the cloud path.** Vision model is better there. PaddleOCR belongs to the on-prem stack, if and when it is built.
- **Computing duty.** Rates change, liability is real, the licensed agent owns the call. Deferred.

## Weak points that survived

- Accuracy on stamps, handwriting, rotated scans and merged table cells is **unmeasured**. There is no eval set yet.
- **Review rate is unknown**, and it is the business model. A document that costs $0.03 in tokens and 4 minutes of a clerk is a $2 document.
- The reader-disagreement check only works on digital PDFs with a text layer; pure scans get weaker validation.
- Kenyan specifics are **unverified** — current IDF/RDL/VAT treatment, iCMS required field set, TradeNet message schemas, KWATOS port-pass format.
- With caller-supplied grouping, **under-grouping is now the caller's failure**, but they will still blame the platform when reconciliation misses a conflict.
- "Accommodate everything" is safe at the API surface (a group of one is a group) but has no natural stopping rule. Nothing here says what you decline next.

## v1 scope (decided)

**v1 is the caller-grouped extraction API. No grouper.**

- `POST /v1/extract` accepting `job_ref` (optional, opaque, caller-supplied) + `documents[]` (1..n). Async: 202 + `result_id`, completion via webhook with polling fallback.
- The same `job_ref` may be called repeatedly; results accumulate under it and later calls reconcile against earlier stored results. Still assertion, never inference.
- Ingestion is caller-side. No mailbox watching, no `.eml` parsing, no matching heuristics, no unmatched queue, no cross-email job assembly.
- All seven document type schemas defined; segmentation and extraction live for all seven. Commercial invoice is the only type with a full Tier-1 check set in v1; the other six route to 100% review.
- Output per document: fields + provenance bbox, `checks[]`, `conflicts[]`, `decision`.
- Trust ramp scaffolding in place but v1 runs at UNSEEN/OBSERVED = 100% review. TRUSTED + sampling once there is volume.
- No ERP posting in v1 — the client consumes the response/webhook. No duty/CIF computation. No batch intake. No on-prem stack.

**Deferred to v2:** the grouper (deterministic only: one email/thread = one job, never fuzzy), email ingestion, ERP posting with idempotency keys and ordered commit, on-prem provider stack, TRUSTED ramp with sampling.

**Note:** the grouper is an *ingestion* feature, not an on-prem feature. It is equally needed for cloud email intake if you ever sell to firms without an API. Do not file it under "on-prem" or cloud will silently end up with no intake story.

**Accepted v1 consequence:** the buyer must be technical. SMB clearing firms without a TMS cannot be served in v1. Use concierge ingestion (manual grouping feeding the API) to acquire the eval set and early revenue without building the grouper.

## Resolved when the spec was written

- **No VAT, levy or duty computation of any kind** — every assessment figure is emitted as stated on the document. Supersedes the narrower "no duty/CIF" wording.
- **Port pass is caller-supplied.** v1 integrates with no port, terminal, customs or single-window system. The KPA/KWATOS, iCMS and TradeNet references describe the destination of the data, not systems the platform calls.
- **Verification memo** is an internal CFS document or client-supplied, with the highest layout variance of the seven types.
- **Custom entry field set** is provisionally derived from the WCO Data Model and EAC practice, recorded in the spec's `document-types.md` and explicitly unverified.
- **Viability thresholds** are set from measurement, not in advance — which makes the labelled eval set a prerequisite rather than a later task.

## Do first

1. Build a labelled eval set before choosing any model: ~20–30 real documents per type, from a working clearing agent. Every architecture choice above is unverifiable without it.
2. Confirm with a Kenyan clearing agent: required iCMS/TradeNet fields, IDF/RDL/VAT treatment, where the port pass actually originates, and the seven-API posting order in the target ERP.
