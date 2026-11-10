# ICM + Agent-Frameworks – Dimension 1 Digest (R1-1)

Accessed: 2026-11-09. Tools used: 10/10 (5 websearch, 5 webfetch). Sources actually readable: 3 official docs.

Method check: `websearch` returned `No search results found` for every query tried. Pivoted to `webfetch` of primary docs only. Not exhaustive academic coverage.

## Findings (claims)

- C-L1: LangGraph is a low-level orchestration framework/runtime for long-running, stateful agents, defined as graphs of shared state, nodes, and edges. Source: https://docs.langchain.com/oss/python/langgraph/overview — Publisher: LangChain — pub_date: null — accessed: 2026-11-09 — confidence: high — class: framework
- C-L2: LangGraph does NOT prescribe prompts or fixed agent architecture, and does NOT require LangChain. Source: same LangGraph overview — confidence: high — class: framework
- C-L3: LangChain now formally splits harness / framework / runtime / platform (Deep Agents / LangChain / LangGraph / LangSmith). Source: same — confidence: medium — class: landscape. Note: single-publisher taxonomy, needs cross-check.
- C-L4: OpenAI Agents SDK is production-ready, minimal-primitive successor to Swarm, built on Agents + Handoffs/Agents-as-tools + Guardrails. Source: https://openai.github.io/openai-agents-python/ — Publisher: OpenAI — pub_date: null — accessed: 2026-11-09 — confidence: high — class: framework
- C-L5: OpenAI SDK defaults to Responses API for OpenAI models; direct Responses API use = own-the-loop, SDK use = managed turns/handoffs/guardrails/sessions. Source: same — confidence: high — class: framework
- C-L6: Sandbox agents / realtime / voice are first-class OpenAI SDK paths. Source: same — confidence: medium — class: landscape. Do not cite model version as compatibility claim.
- C-L7: CrewAI is multi-agent "crews and flows" product (Agents, Tasks & Processes, Flows) marketed as production-ready with guardrails/memory/knowledge/observability. Source: https://docs.crewai.com/ — Publisher: CrewAI — pub_date: null — confidence: medium — class: framework. Marketing register flagged, not adopted.
- C-L8: Legacy LangGraph GitHub Pages URL redirects to docs.langchain.com (docs consolidation). Source: https://langchain-ai.github.io/langgraph/ — Publisher: LangChain — confidence: medium — class: ecosystem

## Could NOT find (ICM)

- Queries attempted (all websearch, no hits): `ICM methodology AI agents`, `ICM acronym agent building`, `LLM agent frameworks comparison 2026`, `AI agents`, `LangGraph framework`.
- No result defined an "ICM methodology" as a named standard/acronym for building AI agents in the 3 official docs read.
- ICM expansion remains unverified belief from this dimension alone — NOT invented.
- AutoGen: https://microsoft.github.io/autogen/ fetch returned only placeholder redirect to example.com/stable — no usable content. No claim on AutoGen status.

## Leads for R2

- ICM under other expansions: academic/library/standards search, agent-construction literature, grey literature (blog/agency method posts) — local/internal vs global standard?
- Category split (harness vs runtime): verify LangChain split vs CrewAI/OpenAI taxonomies + retrospective.
- OpenAI Swarm → Agents SDK migration: dated changelog + repo history needed for stability/5-year regret.
- CrewAI enterprise pivot: dated release notes + retrospectives (OSS core vs Enterprise roadmap driver).
- AutoGen revival/maintenance: direct GitHub repo + changelog.

## Limits

- No version/compatibility, performance/scale, failure claims evidenced. No two-source verification. No repo time-series.
- Ecosystem health NOT assessed (no contributor velocity, release cadence, backing, 5-year regret).
- Pub dates null (landing pages carry none) — freshness bars require dated sources next round.
