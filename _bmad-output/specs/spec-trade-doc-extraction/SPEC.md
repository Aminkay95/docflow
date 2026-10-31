---
id: SPEC-trade-doc-extraction
companions:
  - document-types.md
  - provider-notes.md
  - stack.md
sources:
  - ../../forge/doc-extraction-pipeline/forged-idea.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Kenyan Trade-Document Extraction API (v1)

## Why

**A pain to solve.** Kenyan clearing agents and freight forwarders rekey trade documents by hand into KRA iCMS, KenTrade TradeNet and their own ERP. A single shipment arrives as an invoice, a packing list, a bill of lading, a delivery order, a port pass, a customs entry and a verification memo — spread across files, emails and days, in inconsistent layouts. Retyping is slow and transposition errors in weights and values surface only after a declaration is filed, when they cost penalties and rework.

The extraction technology is now good enough, but the market is full of tools that return confident JSON with no way to tell which values are trustworthy. What is missing is extraction that emits **only what the paper says**, proves where each value came from, and refuses to pass silently when two documents disagree.

## Capabilities

- **CAP-1**
  - **intent:** A caller submits a group of one or more trade documents under an optional opaque `job_ref` and receives per-document structured JSON for that group in a single asynchronous operation.
  - **success:** `POST /v1/extract` returns `202` with a result id; the completed result contains exactly one output object per submitted document, correctly grouped, retrievable by webhook and by polling.

- **CAP-2**
  - **intent:** The system splits every submitted file into per-document segments so that a file containing several document types yields several documents.
  - **success:** A single PDF containing a bill of lading, a commercial invoice and a packing list yields three documents with correct, non-overlapping page ranges and correct types.

- **CAP-3**
  - **intent:** The system extracts typed fields for all seven document types, attaching page and bounding-box provenance to every emitted field.
  - **success:** For each of the seven types, a held-out sample document produces fields whose provenance boxes, when rendered over the source page, enclose the text the value was read from; no emitted field lacks provenance.

- **CAP-4**
  - **intent:** The system decides per job whether output may pass without human review, using deterministic checks and falsifiable signals, and states a human-readable reason for every failed check.
  - **success:** A job with a correct invoice passes all invoice checks; a job with a transposed gross weight fails the arithmetic check and returns a reason naming the field, the expected value and the stated value.

- **CAP-5**
  - **intent:** The system detects when two documents in the same asserted group disagree on the same field and reports the disagreement rather than resolving it silently.
  - **success:** Where the invoice states 18,450 kg and the packing list states 18,540 kg, the result contains one conflict entry naming the field and both source documents, and the job decision is review — while both documents still report their own stated value.

- **CAP-6**
  - **intent:** Reviewers work two distinct queues — extraction errors and genuine document conflicts — and every correction is captured as a labelled record.
  - **success:** A reviewer can resolve an extraction error and a document conflict through separate flows; each resolution stores field, prior value, corrected-or-confirmed outcome and reason, and the two queues report separate counts.

- **CAP-7**
  - **intent:** The system records trust state per client, supplier, document type and template fingerprint so review depth can be reduced later without redesign.
  - **success:** Every processed document resolves to a trust record; a previously unseen template fingerprint starts at full review; a human correction demotes the matching record.

- **CAP-8**
  - **intent:** A caller may submit further documents under an existing `job_ref`, and the new results reconcile against what that job already holds.
  - **success:** Submitting an invoice under `job_ref` on day one and a bill of lading under the same `job_ref` on day three produces one job containing both documents, with conflicts evaluated across the pair.

- **CAP-9**
  - **intent:** All seven document types have a defined output schema built on recognised standard spines, with market-specific detail isolated in a per-type extension block.
  - **success:** Invoice and proforma outputs validate against a UBL 2.1 / Peppol BIS Billing 3.0 derived structure; custom entry against a WCO Data Model derived structure; the three local types carry their detail only inside the extension block.

- **CAP-10**
  - **intent:** The system assesses the legibility of every page before reading it and either improves the page or declares it unreadable, rather than returning a guess.
  - **success:** A photographed, skewed, low-resolution page is detected, corrected and read; a page that remains illegible after correction returns an explicit unreadable outcome naming the reason, and never returns fabricated field values.

- **CAP-11**
  - **intent:** The system reads long and dense tables without silent truncation or row loss.
  - **success:** A packing list with 200 line items returns all rows present in the source, or reports the exact rows it could not resolve; a truncated result is never presented as complete.

- **CAP-12**
  - **intent:** Review depth and pass criteria are configurable policy, so target review rate can be set and changed without a code change, and the effect is measurable.
  - **success:** Raising or lowering the review threshold changes the observed review rate on the evaluation corpus without a deployment, and every decision records which policy version produced it.

## Constraints

- **Emitted values are as-stated on the page.** Arithmetic is a validation oracle only and is never emitted as data.
- **No duty, levy, VAT or CIF computation.** Every such figure is read from the page as stated. Emit incoterm plus goods value, freight and insurance as separate stated fields. No derived customs-value or tax field exists.
- **Model confidence never gates.** Confidence and logprobs may order the review queue; only deterministic checks and falsifiable signals decide pass-or-review.
- **Provenance is mandatory.** A field without page and bounding-box provenance is not emitted.
- **Segmentation always runs**, never conditionally on a guess about whether a file is mixed. It is cheap relative to extraction and is the safety net.
- **Page conditioning always runs before vision**: orientation detection and correction, deskew, crop, DPI normalisation and denoise. Best-effort input is never sent to a model raw.
- **Readers are pluggable, and no reader is privileged.** Three implementations sit behind one interface: embedded text layer, PaddleOCR (deterministic text and table structure), and a vision model. A per-page routing policy chooses which readers run.
- **PaddleOCR ships in v1**, because the vision provider caps every image at roughly a 1024-token, 1300×1300-equivalent downscale — which makes long tables unreadable in a single vision pass. It is used for table structure and as a deterministic second reader on scans, never as the primary reader for stamps, handwriting or skewed photos.
- **Dense regions are read from crops at native resolution**, never from a downscaled whole page.
- **Every service is containerised and runnable by Docker Compose.** `docker compose up` starts the whole stack from a clean clone with no host prerequisite beyond Docker, and production runs the same images as development.
- **Documents are purged six hours after the job reaches a terminal state.** Terminal means posted or review-resolved. A job with an open review item or an unresolved conflict is not terminal, and its clock has not started. Expiry is enforced by a scheduled sweeper with a test and an alert, never by convention.
- **Purging the document does not purge the extraction.** The immutable extraction record — stated values, provenance coordinates and document hash — outlives the file, because it is what answers a dispute months later. Its retention is a separate policy from the document's.
- **The extraction record is retained for the life of the account, plus ninety days after termination**, then deleted. It is exportable in full at any time on request, and deleted on request before expiry. Retention is defensible only because the customer can always take it with them.
- **Observability never retains document content.** Logs, traces, error reports and metrics carry hashes, model versions, timings, token counts and field outcomes — never page images, never document text, never prompt payloads. A trace store that keeps document bodies is a second retention policy nobody wrote down.
- **Provenance is portable.** Every emitted field carries page index, bounding box and document hash, so a client can overlay the box on their own copy of the document without the platform having to keep it. Provenance that only works while the platform holds the file is a promise that expires.
- **Output is validated against the schema on every call.** Provider JSON mode guarantees valid JSON, not schema adherence, so a validation-and-repair loop is part of the pipeline.
- **Unreadable is a valid outcome.** A page that cannot be read after conditioning returns an explicit unreadable status with a reason; it is never filled with a plausible guess.
- **Two reader paths where both exist.** Digital documents are read from the embedded text layer and by vision; disagreement between the two is a first-class signal, not a tie to be broken silently.
- **Grouping is input, never inference.** The service validates the asserted group and reports conflicts, but never regroups, splits or matches documents on its own.
- **Cloud-first with a provider adapter** behind every model call, so the inference stack is swappable. No provider-specific type leaks into the domain model.
- **Standard spines, not invented schemas.** UBL 2.1 / Peppol BIS Billing 3.0 for the invoice family; WCO Data Model for custom entry; proprietary extension blocks for delivery order, port pass and verification memo.
- **National context is a field value, never a field name**, so the model is not locked to Kenya.
- **Async contract.** Submission returns a result id; completion is delivered by webhook with polling as fallback.
- **Deterministic idempotency key per document** (content hash plus job reference) on every stored record and every future outbound call.
- **Payload limits are explicit and enforced at the edge:** a group carries at most 20 documents, and any single file above 25 MB is rejected before processing.
- **Multi-shipment containers are refused loudly**, naming the count and instructing the caller to split; never partially processed.
- **Email may alert; only the authenticated platform may resolve.**
- **Data model:** file is a container, page is the atom, segment is the unit of extraction.
- **Market is Kenya:** KRA iCMS, KenTrade TradeNet, KPA / KWATOS.

## Non-goals

- No email or mailbox ingestion, no grouper, no job assembly beyond the caller-supplied `job_ref`.
- No posting to any ERP or customs system in v1; clients consume the response and webhook.
- No on-premise deployment and no local inference stack.
- No duty, levy or CIF computation.
- No batch or multi-shipment intake.
- No automated trust-ramp promotion; v1 runs entirely at full review.
- Not a general-purpose document AI: scope is the seven trade document types in `document-types.md`.

## Success signal

A clearing agent submits one group — invoice, packing list and bill of lading — and receives per-document JSON where every value traces to a box on a page, the transposed gross weight is caught as a named conflict, and no correct value was altered or invented by the platform.

## Assumptions

- Every document in a submitted group relates to one bill of lading, asserted by the caller and not verified by the platform.
- The field schema is invariant across document layouts; only the mapping from a given layout to those fields varies. This is the assumption the whole product rests on.
- Real Kenyan trade documents are obtainable from a working clearing agent for the labelled evaluation set.
- A large fraction of intake arrives as digital PDFs with an embedded text layer, making a cheap reader path viable.
- v1 buyers are technical and can call an API; clearing firms without a TMS are out of reach until v2.
- v1 inference runs on DeepSeek. Vision support and its limits are verified and recorded in `provider-notes.md`; `deepseek-v4-pro` is text-only, so the vision reader runs on `deepseek-flash` and Pro is used only for reasoning over extracted text.
- Model choice is provisional and decided by measured accuracy per document type, not by vendor preference or price alone.
- Review rate and pass thresholds are owned by the product owner, not the engineering team. The engineering obligation is to make those thresholds configurable, versioned and measurable — never to set them in code.
- The labelled corpus is supplied as complete jobs, not as loose document samples: a set of full shipment folders is worth far more than the same count of unrelated documents, because conflicts and reconciliation can only be tested on documents that relate.
- The custom entry field set is provisionally derived from the WCO Data Model and standard EAC import-declaration practice, recorded in `document-types.md`. It is an educated guess pending confirmation from a working clearing agent, not a verified iCMS contract.
- All documents, including the port pass, arrive from the caller. The platform integrates with no port, terminal, customs or single-window system in v1.
- The verification memo is an internal container-freight-station document or a client-supplied document, not a standardised external certificate. Its layout variance is expected to be the highest of the seven types.
- Viability thresholds are not set in advance. Target accuracy per document type and target review rate are established from measurement on the labelled evaluation set, and the trust ramp has no destination until those numbers exist.
- The labelled evaluation set is a prerequisite, not a deliverable of a later story: roughly twenty to thirty real documents per type obtained from a working clearing agent.

## Open Questions

None outstanding. Payload limits are set at 20 documents per group and 25 MB per file; a client demanding more is a v2 chunking problem, not a v1 one.
