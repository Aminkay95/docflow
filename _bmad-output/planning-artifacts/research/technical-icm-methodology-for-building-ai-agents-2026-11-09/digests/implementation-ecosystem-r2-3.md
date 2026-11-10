# Ecosystem + Implementation Verification (R2-3)

Accessed: 2026-11-09. Pub dates are actual GitHub published_at/pushed_at/created_at or HN created_at. Viktor/OTel pages undated — not fabricated.

## Ecosystem/version

- LangGraph latest stable langgraph==1.2.20 published 2026-11-05; SDK/CLI patch 2026-11-06, 3-4 days before access. Source: https://api.github.com/repos/langchain-ai/langgraph/releases?per_page=5 — LangChain AI / GitHub API — pub 2026-11-06 — high. 5-release window shows steady daily feat/fix, no breaking change noted.
- CrewAI latest 1.15.39 published 2026-11-09 (access date); 1.15.38 on 2026-11-08, 1.15.37 on 2026-11-07, 1.15.36 on 2026-11-06, 1.15.35 on 2026-11-03. Source: https://api.github.com/repos/crewAIInc/crewAI/releases?per_page=5 — CrewAI / GitHub API — pub 2026-11-09 — high. Daily cadence over time, no breaking change in window.
- OpenAI Agents SDK pre-release v0.24.0-alpha.7 published 2026-11-08, 1 day before access. Source: https://api.github.com/repos/openai/openai-agents-python/releases?per_page=5 — OpenAI / GitHub API — pub 2026-11-08 — medium (proves active dev, stable cadence NOT retrieved — JSON truncated).
- AutoGen last release python-v0.7.5 dated 2025-09-30, 13 months before access; prior 0.7.1 2025-07-28, 0.7.2 2025-08-07, 0.7.3/0.7.4 2025-08-19. Source: https://api.github.com/repos/microsoft/autogen/releases?per_page=5 — Microsoft / GitHub API — pub 2025-09-30 — high. Trajectory stalled after Sep 2025 despite push below.
- AutoGen NOT archived, push on 2026-10-28 (12 days before access), 61,762 stars, 9,390 forks, 1,168 open issues. Source: https://api.github.com/repos/microsoft/autogen — Microsoft / GitHub API — pub 2026-10-28 (created 2023-08-18) — high. custom_properties.activeRepoStatus:false noted, not authoritative.
- Flakestorm repo flakestorm/flakestorm created 2025-12-28, last pushed 2026-04-16, 46 stars, 5 forks. Source: https://api.github.com/repos/flakestorm/flakestorm — pub 2026-04-16 — high.
- HN Flakestorm story 46587182 dated 2026-01-12, author frankhumarang, reports exact 5.2% robustness figure. Source: https://hn.algolia.com/api/v1/search?query=Flakestorm — Hacker News / Algolia — pub 2026-01-12 — high as HN text, low as general fact.
- OTel GenAI conventions moved from opentelemetry.io to github.com/open-telemetry/semantic-conventions-genai; legacy /docs/specs/semconv/gen-ai/ now only moved-notice, no version. Source: https://opentelemetry.io/docs/specs/semconv/gen-ai/ — OpenTelemetry — undated — medium for move only. gen_ai.* version NOT verified.

## Implementation

- Viktor thread $11.35 without caching vs $2.07 with caching (81.8% cut), ~2.17M input tokens, Claude Opus 4.8. Source: https://viktor.com/research/how-we-built-viktor-around-prompt-caching/md — Viktor — undated — high as reported, medium as generalizable (single-vendor retrospective).
- Viktor: 9,800+ tools as SDK functions in code instead of prompt schemas to keep cache prefix byte-stable. Same — high as reported architecture.
- Viktor: caches fragile — exact prefix match + ~5-min Anthropic TTL, per-provider adapters (Anthropic/OpenAI/edge). Same — high for fragility-as-described.
- Flakestorm HN asserts 5.2% (57/60 failures), 0% encoding, 0% prompt-injection, ~30s latency under stress. Source: https://hn.algolia.com/api/v1/search?query=Flakestorm (story 46587182) — frankhumarang / HN — pub 2026-01-12 — high verbatim, low corroborated.

## Verdicts

- AutoGen: MAINTENANCE / RELEASE-STALLED (not active, not archived, not revived) — 13 months since release exceeds ≤1-mo version and ≤6-mo ecosystem bars despite archived:false + 2026-10-28 push. Medium-High.
- LangGraph: ACTIVE — 2026-11-05/06 within 4 days. Fresh.
- OpenAI Agents SDK: ACTIVE (ALPHA) — 2026-11-08 within 1 day. Fresh but pre-release; stable cadence unverified.
- CrewAI: ACTIVE — 2026-11-09 same-day, daily 2026-11-03 to 2026-11-09. Fresh.
- Flakestorm 5.2%: UNVERIFIED — single-source anecdote. Origin single-author HN 46587182 (2026-01-12, 0 comments, 1 point) + earlier Show HN 46495434 (2026-01-05) without numbers. HN Algolia: no second publisher. Repo confirms tool exists, not figure. One post-mortem = anecdote.
- Viktor caching/tools: CONFIRMED as Viktor-reported (matches R1). No independent replication; not vendor-neutral truth.
- Tessary/Oimo/OTLP Langfuse: Not re-verified (budget). OTLP move confirmed only. gen_ai.* + Langfuse burden UNVERIFIED.

## Could NOT find (11 calls, 8 publishers)

- LangChain harness/framework/runtime/platform split vs CrewAI/OpenAI: NOT fetched (needs 3 docs fetches).
- langchain-ai.github.io → docs.langchain.com: NOT confirmed.
- Flakestorm second publisher: HN Algolia only; no second publisher found.
- OpenAI stable cadence: only alpha retrieved; JSON truncated.
- OTLP gen_ai.* version + Langfuse burden: legacy page only; Langfuse docs NOT fetched.
- Viktor "workarounds expire per model release": not in caching post; https://viktor.com/research/a-harness-for-agi NOT fetched.
- AutoGen docs intent: pushes vs revival NOT verified; commits not fetched.
- Breaking-change inventory beyond 5-release windows: not read.
