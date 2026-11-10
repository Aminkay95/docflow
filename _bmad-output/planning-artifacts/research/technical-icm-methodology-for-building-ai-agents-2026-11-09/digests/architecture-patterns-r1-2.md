# ICM methodology for building AI agents — Dimension 2 digest (R1-2)

Scope: dominant named architecture patterns, step-by-step construction methodology, ICM mapping. Evidence from sources read this run only.

Method / budget: 10/10 tool calls, 4 distinct sources actually read: 5 websearch (all "No search results found", including control `ReAct agents` — tool failure, not evidence of non-existence), 5 webfetch (3 primaries + 2 DuckDuckGo HTML search pages). ICM claims rest on snippets only; underlying GitHub/arXiv/gist pages NOT fetched (budget exhausted). Accessed: 2026-11-09.

Freshness: Dec 2024 and Jun 2025 sources at/over bars for version/landscape — used only for pattern/methodology with staleness flagged. Performance numbers single-publisher.

## A. Pattern

- C1: Anthropic defines workflows as predefined code paths vs agents as LLMs dynamically directing their own processes/tool use, with augmented LLM (LLM + retrieval/tools/memory) as base block. Source: https://www.anthropic.com/research/building-effective-agents — Publisher: Anthropic PBC — pub_date: 2024-12-19 — accessed: 2026-11-09 — confidence: high — class: pattern
- C2: Five recurring workflows: prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer. Same source — confidence: high — class: pattern
- C3: ReAct interleaves reasoning traces with task-specific actions. Source: https://arxiv.org/abs/2210.03629 — Publisher: Yao et al. via arXiv — pub_date: 2023-03-10 (v3) — confidence: high for pattern — class: pattern
- C4: ReAct overcomes chain-of-thought hallucination via Wikipedia API, more interpretable trajectories. Same — confidence: medium — class: pattern
- C5: ReAct beat imitation/RL by 34 and 10 points on ALFWorld/WebShop (1-2 in-context examples). Same — confidence: medium (single-publisher) — class: pattern
- C6: Autonomous agent pattern = LLM using tools in loop, ground truth each step, human checkpoints, stopping conditions. Source: same Anthropic building-effective-agents — confidence: high — class: pattern
- C7: Production Research system = orchestrator-workers: LeadResearcher spawns parallel Subagents, synthesizes; CitationAgent attributes claims. Source: https://www.anthropic.com/research/multi-agent-research-system — Publisher: Anthropic — pub_date: 2025-06-13 — confidence: high for design — class: pattern
- C8: Subagents aid compression via separate context windows + separation of concerns. Same — confidence: medium (vendor own system) — class: pattern
- C9: Multi-agent (Opus 4 lead + Sonnet 4 subagents) outperformed single-agent Opus 4 by 90.2% on internal eval. Same — confidence: medium (single-publisher) — class: pattern
- C10: Token usage explains ~80% of BrowseComp variance (3 factors ~95%); multi-agent ~15x chat tokens (agents ~4x); value must be high; shared-context/tight-dependency domains fit poorly. Same — confidence: medium — class: pattern
- Failures (C7/C9-C10): 50 subagents for simple queries, endless search, duplication, SEO farms over primary sources. Parallel subagents/tool calls cut time up to 90%.

## B. Methodology steps

- C11: Start simplest; single LLM calls with retrieval/in-context examples often enough; add agentic complexity only when demonstrably improves measured outcomes. Source: building-effective-agents — confidence: high (Dec 2024, page warns landscape changed) — class: methodology steps
- C12: Three core principles: simplicity, transparency (show planning steps), ACI via tool docs/testing (poka-yoke tools). Same — confidence: high — class: methodology steps
- C13: Production sequence: (1) think like agents via Console sim, (2) teach orchestrator to delegate (objective/output/tools/boundaries), (3) scale effort to complexity, (4) explicit tool-selection heuristics, (5) start wide then narrow, (6) guide thinking via extended/interleaved thinking, (7) parallelize tool calls. Source: multi-agent-research-system — confidence: high for practice — class: methodology steps. Effort tiers: simple = 1 agent/3-10 calls; direct comparison = 2-4 subagents/10-15 each; complex = >10 subagents. Agent-rewritten tool descriptions cut time 40%.
- C14: Evaluate on ~20 real queries; single-prompt LLM-as-judge rubric (factual accuracy, citation accuracy, completeness, source quality, tool efficiency); retain human testing (SEO bias, edge cases). Same — confidence: high for method — class: methodology steps
- C15: Production hardening: durable/resumable execution with retries/checkpoints, production tracing, rainbow deployments, filesystem artifacts, summarization/external memory/fresh-subagent handoffs. Same — confidence: high — class: methodology steps

## C. ICM-mapping (snippet-only, LOW)

- C16: 2026-vintage methodology ICM (snippets: Interpretable/Interpreted/Intelligent Context Methodology) organizes agent work via filesystem structure instead of framework orchestration code. Sources: DuckDuckGo HTML hits (GitHub, arXiv, gist, LinkedIn, wiki) via https://duckduckgo.com/html/?q=ICM+AI+agent+framework — Publisher: DuckDuckGo (snippets) — pub_date: not stated — accessed: 2026-11-09 — confidence: low — class: ICM-mapping
- C17: ICM uses numbered folders = workflow stages, markdown files = prompts/context, local scripting; one agent reading right file at right moment does framework work. Same — confidence: low
- C18: ICM workspaces declare what to load vs skip and what agent should do; Unix-pipeline/modular-decomposition/multi-pass-compilation analogies. Same — confidence: low
- C19: Exact phrase "ICM methodology" + AI agents = zero DuckDuckGo results; broader ICM + AI agent = multiple repos/papers — consistent with very recent, naming-unstable idea. Same — confidence: medium for observation, low for maturity inference

ICM synthesis (low): Orthogonal to ReAct/workflow/multi-agent, not a peer. Orchestration/anti-framework layer: replaces CrewAI/LangChain/AutoGen coordination code with folders/markdown + single sequential agent. Overlaps C11-C12 (start simple, avoid opaque layers, ACI/context design). Diverges from C7/C13/C15 (code-delegated parallel subagents, effort budgets, tracing, durable execution vs filesystem state + file-read handoffs). No evidence ICM evaluated at C9-C10 scale or addresses token scaling (C10), sync bottlenecks, or eval (C14).

Maturity: snippets show multiple independent repos (RinDig/Interpretable-Context-Methodology, NathanForster/ICM), gist guide, arXiv 2603.16021, LinkedIn guide, blog field guide, wiki, video — emergence signals, NOT production adoption. Wiki attributes intro March 2026 (Jake Van Clief / David McDermott) — unverified.

## Leads for R2

1. Fetch ICM primaries: https://github.com/RinDig/Interpretable-Context-Methodology, https://github.com/NathanForster/ICM, https://arxiv.org/abs/2603.16021, gist — verify full name(s), date, authors, stage/role/artifact definitions, eval numbers.
2. Fetch https://www.anthropic.com/engineering/managed-agents + docs (current approach) to cure Dec 2024 staleness.
3. Second-publisher corroboration for C5, C9-C10; repo-metric trajectories not snapshots.
4. Missing named patterns: Reflexion/self-refine, plan-and-execute, tool-use loop primaries.
5. Contradiction: C11 avoid frameworks vs ICM no framework vs C13-C15 frameworks as code patterns — which holds for single-long-horizon vs parallel-breadth?

## Could NOT find

- No ICM mention in any primary pattern source read. Zero hits in Anthropic building/multi-agent and ReAct abstract.
- No "ICM methodology" exact-phrase hit. Do not invent acronym — snippets give 3 variants (Interpretable/Interpreted/Intelligent).
- No ICM steps/roles/artifacts, maturity, production numbers verified. Only snippet-level folders/markdown/load-skip.
- No corroboration for C5/C9-C10. Remain single-publisher.
- Websearch 5/5 empty — search coverage incomplete.
