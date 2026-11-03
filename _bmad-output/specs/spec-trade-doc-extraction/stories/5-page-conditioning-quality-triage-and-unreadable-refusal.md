---
title: 'Page conditioning, quality triage and unreadable refusal'
type: 'feature'
created: '2026-11-03'
status: 'done'
baseline_commit: '7fefd5fdb4a538cd67210e73ee94ba00ee6a3fc7'
route: 'dispatch'
review_loop_iteration: 0
context:
  - 'docs/01-architecture.md'
  - 'docs/07-implementation-blueprint.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Prepared pages are stored as rendered, with pending quality state. Vision can receive skewed, noisy or illegible evidence without correction or an explicit refusal.

**Approach:** Add deterministic page conditioning before vision and during worker preparation. Correct supported orientation, perspective/skew, borders, DPI and noise; measure the remaining image quality and return a versioned conditioned or unreadable result with a reason.

## Boundaries & Constraints

**Always:** Preserve the source file, page index and segment identity. Keep a reversible coordinate transform between source and conditioned images, including perspective correction, so provenance remains aligned. Assess every page and retain diagnostic measurements, applied operations, policy version and refusal reasons. Bound decoded pixels, enlargement and analysis work. Use Pillow and lazy worker-side OpenCV imports; preserve the API import boundary. Keep thresholds configurable and record their effective values. Never send unreadable pages to any reader. Preserve unconditional segmentation and preparation retry/idempotency behavior.

Orientation uses source/EXIF hints and deterministic image evidence; unsupported or ambiguous correction must be reported rather than represented as certain. Upscaling does not create detail and cannot erase a low-resolution refusal. Quality is an image-based heuristic, not proof of semantic readability or field correctness. OCR confidence is unavailable until story 6 and must not be invented. Conservative defaults are provisional until story 7 supplies labelled documents.

**Never:** Call a model to condition or score pages, fabricate pixels or extracted values, remove stamps or handwriting as noise, crop away uncertain content, approve documents using a score, or silently accept a failed correction. No PaddleOCR reader, routing policy, invoice extraction activation, review UI, decision gate or real-document uploads in this story. Document lifecycle activation remains story 8.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Clean digital page | Rendered image and actual character boxes | Stable conditioned image and aligned geometry; measured quality | No invented boxes |
| Photograph | EXIF rotation, skew or detected page quadrilateral | Supported rotation, perspective/deskew, conservative crop and denoise | Retain uncertainty; refuse when safe correction is impossible |
| Poor image | Blank, blurred, low contrast or insufficient source detail | Explicit unreadable status and stable reason codes | No provider calls or field candidates |
| Ambiguous border or orientation | Weak geometric evidence | No destructive speculative transform; report uncertainty | Refuse if required legibility/orientation cannot be established |
| Mixed file | Readable and unreadable pages | Persist each page outcome and preserve all segment ranges | Readers skip unreadable pages; retain their reasons |
| Unsafe image | Corrupt bytes or excessive dimensions/enlargement | Bounded named failure before expensive allocation | Existing worker failure path; no half-written page set |
| Retry | Preparation repeated after success or interrupted storage | One consistent page/segment/document set | Preserve existing retry and cleanup guarantees |

</frozen-after-approval>

## Code Map

- `services/page_model.py::PageRendering`, `_render_pdf`, `_render_image` carry JPEGs, dimensions, DPI, text and normalized character boxes. PDF rotation already affects rendered geometry; do not apply it twice. Capture image EXIF before JPEG encoding loses it.
- `workers/handlers/prepare_file.py::_render_and_store`, `_StoredPage`, `handle` stream rendering/storage, then atomically replace page/segment/document rows. Condition before storing derivatives; retain segmentation text.
- `db/models/page.py` already defines score, conditioning JSON, version, status, reason and conditioned timestamp. `db/repositories/pages.py::insert` currently forces pending state. Reuse existing columns; no migration is expected.
- `domain/readers.py::PageInput`, `services/readers/vision.py::VisionReader.read`, `services/extraction.py::extract` currently only skip explicit unreadable inputs. Extend trusted conditioning evidence and prevent raw vision calls, including direct reader use and repair attempts.
- `config.py`, `.env.example`, `pyproject.toml`, `Dockerfile.worker` define settings and worker image dependencies. OpenCV/numpy already belong to optional OCR dependencies; make conditioning installable without introducing PaddleOCR into this story's runtime requirements.
- `tests/contract/test_api_imports.py` protects API startup from numpy, OpenCV, PDFium and provider imports. Existing page and reader fixtures need explicit conditioning state where vision is intended.

## Tasks & Acceptance

**Execution:**
- [x] `src/trade_extract/domain/conditioning.py` — define immutable results, quality measurements, reason codes and coordinate-transform contracts without image-library imports.
- [x] `src/trade_extract/services/conditioning.py` — implement bounded deterministic corrections, versioned image quality scoring, conservative refusal, encoded image/thumbnail output and source/conditioned geometry mapping.
- [x] `src/trade_extract/config.py`, `.env.example`, `pyproject.toml`, `uv.lock`, `Dockerfile.worker` — validate policy thresholds and resource budgets; supply worker conditioning dependencies without eager API imports.
- [x] `src/trade_extract/services/page_model.py`, `workers/handlers/prepare_file.py`, `db/repositories/pages.py` — preserve orientation hints, condition every prepared page, store corrected derivatives and persist diagnostics/outcomes using existing page columns.
- [x] `src/trade_extract/domain/readers.py`, `services/readers/vision.py`, `services/extraction.py` — require conditioned vision evidence, preserve aligned citations and expose unreadable page reasons without promoting an invalid result.
- [x] `tests/unit/test_conditioning.py`, `test_page_model.py`, `test_readers.py`, `test_extraction.py`, `tests/integration/test_pages_segmentation.py`, `tests/contract/test_api_imports.py` — test the matrix with deterministic synthetic documents and independent expected coordinates; adapt existing fixtures.
- [x] `docs/01-architecture.md`, `docs/07-implementation-blueprint.md` — document actual score inputs, orientation limits, coordinate conventions and provisional policy defaults.

**Acceptance Criteria:**
- Given independently constructed rotated/skewed/perspective page fixtures, when correction succeeds, then image landmarks and mapped provenance boxes align within an explicit pixel tolerance, including inverse mapping.
- Given a poor photograph that remains illegible after correction, when preparation and extraction run, then its page stores an unreadable reason and no reader/provider receives that page.
- Given a mixed readable/unreadable file, when preparation completes, then all pages remain covered exactly once by segments and each page retains its own outcome and diagnostics.
- Given a changed conditioning setting, when the worker processes a page, then persisted policy metadata reflects the effective setting and tests observe its behavior.
- Given API startup, when imports are inspected, then no provider, OCR, OpenCV, numpy or PDFium module is loaded.

## Implementation Notes

Implemented immutable image-free geometry/quality contracts and lazy OpenCV conditioning. Preparation stores corrected JPEGs/thumbnails, actual character rectangles, invertible point transforms, effective policy, measurements, status, reasons and timestamp through existing page columns; no migration was needed. Source uploads and segmentation identities remain unchanged. Worker dependencies now install conditioning independently of PaddleOCR.

Vision requires current-version conditioning and matching validated geometry on initial and repair calls. All readers skip refused pages, and mixed unreadable segments cannot promote an extracted document. Provider/validation failures retain page refusal reasons. No live provider calls or real-document uploads were made.

Review corrections cover TIFF orientation already consumed by Pillow, native-pixel discarded-content checks, retained sheet detail, metadata-driven DPI reduction, local perspective magnification, competing orientation/skew evidence, geometry validation and complete policy diagnostics. Every review finding was patched; nothing was deferred.

Matrix audit: clean/photograph geometry is covered by independent EXIF, skew, perspective and character-diagnostics tests; poor images by refusal and direct-reader tests; ambiguity by sideways/mixed-line/conflicting-skew/marked-surround tests; mixed files by PostgreSQL outcome/coverage and extraction tests; unsafe images by decode/pixel-budget tests; retry behavior by existing PostgreSQL preparation retry, atomic replacement and worker failure tests. Threshold and output settings are explicitly exercised. Defaults remain provisional, and 0-versus-180 image-only orientation remains uncertain as specified.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: enlargement bound | medium | Perspective local magnification is unbounded and setting is unused. Rotation canvas padding itself does not magnify detail. Patch existing warp sizing. |
| Blind 2: adjacent annotations | high | Reproduced: a white stroke at x=98 beside a sheet starting x=100 is discarded by the seven-pixel exclusion. Patch surround guard. |
| Blind 3: sheet detail | medium | Resolution measures full photograph even after a smaller sheet is isolated. Patch retained-detail measurement. |
| Blind 4: competing orientation | high | Reproduced: sideways fixture plus one horizontal line returns conditioned. Patch existing orientation guard. |
| Blind 5: unresolved skew | medium | Non-coherent angles bypass both correction and refusal with no diagnostic. Patch existing skew assessment. |
| Blind 6: DPI detail loss | medium | Reproduced: 800x1000 tagged 600 DPI becomes 400x500 and remains conditioned. Patch downsampling floor. |
| Blind 7: lost refusal reasons | medium | No-document return precedes unreadable return; provider/repair error exits also omit page reasons. Patch result construction. |
| Blind 8: transform validity | medium | vision_ready checks only non-null transform, accepting malformed or dimension-mismatched geometry. Patch existing contract validation and dispatch guard; caller-provided trusted state is not cryptographic attestation. |
| Blind 9: effective output policy | medium | Recorded conditioning-prefixed settings exclude DPI/quality/thumbnail values that change artifacts. Patch metadata. |
| Edge 1: TIFF double orientation | high | Pillow TIFF decode already applies orientation and clears its tag; captured hint survives into a second rotation. Reproduced orientation 6 with swapped decoded dimensions. Patch renderer. |
| Edge 2: adjacent annotations | high | Same demonstrated excluded-band defect as Blind 2. Grouped patch. |
| Edge 3: competing orientation | high | Same demonstrated incidental-horizontal-line bypass as Blind 4. Grouped patch. |
| Edge 4: lost refusal reasons | medium | Same no-document ordering defect as Blind 7. Grouped patch. |
| Verification 1: persisted characters | medium | Geometry fixtures have no characters; diagnostics character mapping could regress undetected. Patch independent transformed-character assertion. |
| Verification 2: direct text refusal | medium | Extraction wrapper masks a regression in direct text-reader filtering. Patch direct mixed-input payload assertion. |

## Verification

- `uv run pytest tests/unit tests/contract` — all existing and new checks pass without network calls.
- `uv run pytest tests/integration` — PostgreSQL-backed page persistence, retry and lifecycle checks pass; report unavailable infrastructure explicitly.
- `uv run ruff check .` and `uv run mypy` — pass.

Final verification: 285 unit/contract tests and 101 PostgreSQL integration tests passed; Ruff and mypy passed. All review findings were patched; nothing was deferred. Existing Starlette/httpx deprecation warning remains.
