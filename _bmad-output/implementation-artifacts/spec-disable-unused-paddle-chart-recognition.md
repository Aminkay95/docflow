---
title: 'Disable unused Paddle chart recognition'
type: 'bugfix'
created: '2026-11-04'
status: 'done'
baseline_commit: '9ed35e47cc32c4ed86283580001714a6a6f36f1f'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** PaddleOCR initializes a large chart-recognition model that the extraction pipeline does not consume, exhausting the local Docker memory budget before documents reach the configured provider.

**Approach:** Explicitly disable chart recognition when constructing PPStructureV3 while preserving OCR and table recognition.

**Decision:** Cap every DeepSeek response at 8,192 output tokens. Large documents that cannot fit must fail or remain incomplete rather than incur an unbounded output charge.

</frozen-after-approval>

## Code Map

- `src/trade_extract/providers/deepseek.py::DeepSeekProvider._call` — builds Chat Completions requests; add the selected output cap and retain usage recording.
- `src/trade_extract/config.py`, `.env.example`, `docker-compose.yml` — expose and forward a validated provider output-token budget without exposing the live key outside the worker.
- `src/trade_extract/services/readers/paddle.py::PaddleReader.inspect` — retain table OCR while replacing PaddleX's eagerly initialized unused chart slot with a lightweight model.
- `src/trade_extract/workers/handlers/extract_segment.py::rehydrate` — currently restores characters and conditioning but no caller-owned `PageFeatures`; do not invent clean-page evidence in this fix.
- `tests/unit/test_providers.py`, `tests/unit/test_routing_tables_paddle.py` — verify the outbound token cap and Paddle construction invariants.

## Implementation Notes

Explicitly disabled PPStructureV3 chart recognition and explicitly retained table recognition. Added a runtime-construction regression test for both invariants. Persisted the Paddle model cache across worker recreation and scoped the live provider key and model configuration to the worker service.

Live verification showed PaddleX 3.3.2 still creates ChartRecognition unconditionally despite the false flag. The disabled slot now points to the already-required lightweight table classifier, avoiding the multi-gigabyte PP-Chart2Table load while chart inference remains disabled.

Full PPStructure inference still exceeded the 3.7 GiB local Docker VM budget. The live cost run therefore uses the existing explicit OCR pixel-budget failure path and continues only with safe vision evidence; this QA-only setting is not a product default.

The live vision request exposed an uncapped DeepSeek output: HTTP 200 arrived, but the worker remained blocked and no usage record was committed. Implementation is paused pending selection of an explicit output-token budget.

The human selected the 8,192-token budget. The provider now sends the validated cap on every request; operator documentation and Compose contract coverage were added. Verification passes with live QA overrides isolated from the test process.

## Review Triage Log

| Finding | Verdict | Evidence / route |
|---|---|---|
| Blind 1: table recognition relies on dependency default | medium | Real forward-compatibility risk; explicitly enable table recognition and assert it. |
| Blind 2: Paddle model cache is ephemeral | medium | Recreating the worker discarded multi-gigabyte downloads; add a dedicated persistent cache volume. |
| Blind 3: provider key exposed to unrelated services | medium | Shared environment passed the billing credential to API, migration and maintenance; scope provider configuration to worker. |
| Review 2 blind 1: stale pending-selection note | low | The historical append-only note became stale after the human selected 8,192; appended the resolution rather than rewriting history. |
| Review 2 blind 2: mojibake in spec | false | `rg` reads actual UTF-8 em dashes at every cited location; mojibake came from the reviewer's rendering. |
| Review 2 blind 3: operator documentation missing | medium | The public budget setting lacked purpose/range guidance; patched `docs/08-operations-and-decisions.md`. |
| Review 2 blind 4: `.paddleocr` cache not persisted | false | This worker path imports PPStructureV3/PaddleX, whose observed model writes and configured cache are under `.paddlex`; `.paddleocr` is not used by the changed path. |
| Review 2 blind 5: fake backend cannot prove placeholder compatibility | false | Live pinned-runtime logs showed the final eager slot instantiate `PP-LCNet_x1_0_table_cls` and proceed into inference without loading Chart2Table. |
| Review 2 blind 6: spec title narrower than changes | low | The frozen decision explicitly includes the provider budget; the title is cosmetic and changing spec-only prose adds no runtime value. |
| Review 2 blind 7: live narrative lacks reproducibility detail | low | Local QA artifacts retain the run inputs and outcomes; expanding spec-only prose would not improve the runtime fix. |
| Review 2 verification 1: Compose token forwarding untested | medium | A misspelt worker mapping could silently restore the 8,192 default; added a non-default rendered-Compose assertion and isolation checks. |
| Review 2 verification 2: cache mount untested | medium | Removing or retargeting the mount would reintroduce downloads; added exact named-volume and target assertions. |

## Verification

- `PROVIDER=mock OCR_MAX_PIXELS=40000000 uv run pytest tests/unit tests/contract -q` — 377 passed with one existing Starlette/httpx deprecation warning.
- `uv run pytest tests/contract/test_docker_compose.py -q`, `uv run ruff check .`, and `uv run mypy` — passed.
- Rebuilt the worker image and verified the live container receives the 8,192-token cap, the provider credential only in the worker, and the persistent PaddleX cache mount.
- Six public-document QA samples reached terminal results: one completed and five failed conservatively or required review. Every successful billed response respected the 8,192-token cap. Full local Paddle inference remains outside the 3.7 GiB Docker VM budget, so live QA used the explicit OCR-unavailable fallback; no product default was weakened.
