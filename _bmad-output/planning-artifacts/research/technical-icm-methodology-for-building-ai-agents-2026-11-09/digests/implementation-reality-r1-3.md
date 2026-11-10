# Dimension 3 — Implementation reality (ICM / agent-building) (R1-3)

## Findings

- Claim: 40-step agent thread on Claude Opus 4.8 re-sends ~2.17M input tokens while transcript only ~85K, costing $11.35 without prompt caching vs $2.07 with caching (81.8% cut), caching also cuts time-to-first-token. {source: https://viktor.com/research/how-we-built-viktor-around-prompt-caching/md — publisher: Viktor — pub_date: 2026-06-08 — accessed: 2026-11-09 — confidence: high — class: ops-burden}

- Claim: Team building synthetic-user agents ~1 year: massive eval set prohibitively costly — evaluation itself ~5x costlier than running agent (many narrow intents), long-running sessions never reliable enough. {source: https://hn.algolia.com/api/v1/search?query=AI%20agents%20evals%20production&tags=story&hitsPerPage=5 (story 50033189) corroborated by https://github.com/tessaryai/tessary — publisher: Hacker News / Tessary (Akhil) — pub_date: 2026-10-10 — accessed: 2026-11-09 — confidence: medium — class: retrospective}

- Claim: 2-3 orders cheaper per-trace classifiers + SOTA triage on flagged traces tested ~5x cheaper than sampled evals at 1M traces, reliability cost falling as agent improves. {source: same HN story 50033189 — publisher: Hacker News / Tessary — pub_date: 2026-10-10 — confidence: low — class: ops-burden}

- Claim: Financial-agent teams: offline evals cannot cover every question/session; LLM judge in Langfuse too expensive beyond small slice, some failures slip through — added no-LLM-judge release-health checker on tool calls/retries/loops/output length/cost per turn over all production traces. {source: https://github.com/oimo-ai/apgar (corroborated by HN story 50292363) — publisher: GitHub / Oimo AI (jtkm6) — pub_date: 2026-10-29 — accessed: 2026-11-09 — confidence: medium — class: ops-burden}

- Claim: Tool schemas alone blow past context; tool-picking accuracy degrades as toolset grows; at 9,800+ integrations Viktor replaced per-tool schemas with real-code SDK calls to keep prompt prefix small and byte-stable for caching. {source: https://viktor.com/research/how-we-built-viktor-around-prompt-caching/md — publisher: Viktor — pub_date: 2026-06-08 — confidence: high — class: tooling}

- Claim: Prompt caches fragile — exact prefix match required, Anthropic TTL ~5 min — so cache-friendly agents forbid timestamps in system prompt, treat threads as append-only logs, time compaction (hot ~50K / idle ~16K; delayed compaction at 3 min inside 5-min TTL) around cache death, per-provider adapters. {source: same Viktor research — pub_date: 2026-06-08 — confidence: high — class: tooling}

Implication for ICM follower: contend with quadratic re-send cost (cache-first architecture), evals sampling gap (offline evals + judged samples miss failures; need cheap all-traces monitoring + first-hours release comparison), tool/context sprawl (dynamic discovery invalidates caches; internal-tool access is bottleneck, not reasoning). No ICM step list evidenced this round.

## Leads for R2

- Flakestorm adversarial result (HN story 46587182, 2026-01-12): 5.2% robustness, 0% prompt-injection/encoding, ~30s latency under stress — needs second publisher.
- OTLP gen_ai.* ingestion + PII redaction and Langfuse/ClickHouse self-hosting burden (Tessary, Apgar) — chase primaries for version/compatibility + self-host ops cost.
- Viktor AGI-harness claim workarounds expire with each model release — chase for learning-curve/maintenance burden.

## Could NOT find

- Websearch queries "ICM methodology AI agents", "building AI agents production", "AI agents operational burden" returned no results; HN Algolia "building AI agents production" and "AI agents evals production" returned no ICM implementation guide/spec/step list. No ICM implementation guide found this round.
- No 6-12-month post-mortem with independent verification; Tessary and Apgar are vendor Show-HN retrospectives (single publisher each), star/fork snapshots only (Tessary 20★/1 fork, Apgar 9★/1 fork) — no over-time metrics due to budget.
- Budgets: 9/10 tool calls; 6 webfetches + 3 websearch attempts.
