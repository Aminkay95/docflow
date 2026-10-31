# Provider notes — DeepSeek

Verified against `api-docs.deepseek.com` on 31 October 2026. Prices and capabilities move; re-verify before any contract or costing decision.

## Model matrix

| Model | Version | Vision | Context | Max output | JSON output | Tool calls |
|---|---|---|---|---|---|---|
| `deepseek-flash` | DeepSeek-V4.1-Flash-1017 | Yes | 1M | 384K | Yes | Yes |
| `deepseek-v4-pro` | DeepSeek-V4-Pro-0813 | **No** | 1M | 384K | Yes | Yes |

Legacy names `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are accepted but retired; requests are served by the Flash model.

**The split is forced:** `deepseek-v4-pro` cannot read images. The vision reader must run on `deepseek-flash`. Any use of the Pro model is text-only — for example reasoning over already-extracted text.

## Vision constraints

- Formats: JPEG, PNG, GIF, WebP, detected from file content rather than declared MIME type.
- Images are accepted in `user` messages only. An image in a `system` or `assistant` message returns `400`.
- Max 600 images per request.
- Max 32 MiB per image via base64 or external URL; 64 MiB via Files API `file_id`.
- Request body limit 48 MiB. Base64 inflates payloads by roughly one third.
- Max image dimension 8192 px per side, dropping to 4096 px per side when a request contains 15 or more images.
- **Every image is resized before inference:** below roughly 544×544 it is scaled up; larger images are scaled down to roughly a 1300×1300-equivalent pixel count.
- **Hard cap of 1024 tokens per image**, regardless of how large the source image is.

## Consequences for this product

1. **Do not send whole pages and expect fine detail.** A 300 DPI A4 scan is about 2480×3508; the provider reduces it to a 1300×1300-equivalent. Container numbers, HS codes and dense table cells will not survive that. Read them from region crops sent at native resolution, not from a full-page downscale.
2. **Long tables need a separate reader.** The token cap makes a 200-row packing list unreadable in one vision pass. Table structure must come from a deterministic reader — PaddleOCR PP-Structure — with vision supplying header semantics and anything unstructured.
3. **Upload via the Files API for anything large.** Twenty files at the 25 MB product limit cannot share a 48 MiB body, and base64 inflation makes it worse.
4. **Validate every response against the output schema.** "JSON Output" guarantees valid JSON, not schema adherence. Expect a validation-and-repair loop rather than schema-constrained decoding.
5. **Peak hours double the price.** Peak is 01:00–04:00 and 06:00–10:00 UTC, Monday to Friday — which is 04:00–07:00 and 09:00–13:00 East Africa Time. The Kenyan working morning sits entirely inside peak. Backfills belong off-peak; realtime intake cannot avoid it.

## Pricing, per 1M tokens, `deepseek-flash`

| | Off-peak | Peak |
|---|---|---|
| Input, cache miss | $0.15 | $0.30 |
| Input, cache hit | $0.003 | $0.006 |
| Output | $0.60 | $1.20 |

Concurrency limit for `deepseek-flash` is 2500. Cache-hit pricing rewards a stable system prompt per document type — keep prompts and schemas byte-stable so the prefix cache actually hits.
