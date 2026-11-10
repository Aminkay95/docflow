# Round 2 Digest — Patterns Verification + Anthropic Update (R2-2)

Accessed: 2026-11-09. Budget: 12 calls, 7 sources read.

Freshness audit: Building-effective-agents (Dec 2024), Multi-agent (Jun 2025), primaries 2022-2023 are >3 months — usable for patterns (≤2y) NOT landscape. Building-effective-agents now carries banner "Much of the tooling landscape described in this post has changed since December 2024. For our current approach, see Managed Agents" — Dec 2024 stale, Managed Agents (Apr 2026) successor. Managed Agents Apr 2026 ~7 months old at access — >3 months but latest primary. LangGraph create_react_agent deprecated in favor of create_agent.

## Findings

- Anthropic current guidance is Managed Agents (session/harness/sandbox decoupled meta-harness), supersedes Dec 2024 for long-horizon. Source: https://www.anthropic.com/engineering/managed-agents — Anthropic Engineering — pub 2026-04-08 — high — methodology-steps
- Dec 2024: start with LLM APIs directly, frameworks obscure prompts/premature complexity; add complexity only when demonstrably improves outcomes. Source: https://www.anthropic.com/engineering/building-effective-agents — pub 2024-12-19 — high — methodology-steps
- Jun 2025 production: delegation budgets (simple 1 agent, comparisons 2-4, complex 10+), full tracing, durable execution (resume-from-checkpoint, retry, rainbow). Source: https://www.anthropic.com/research/multi-agent-research-system — pub 2025-06-13 — high — methodology-steps
- Managed Agents: session = durable append-only log outside context window, harness crash/reboot via getSession/emitEvent, sandbox cattle via execute/provision. Source: managed-agents URL — pub 2026-04-08 — high
- Resolution: simple holds for predictable workflows; frameworks-as-patterns hold when long-horizon (exceed context, hundreds of turns) or parallel-breadth/high-value/heavy-tool. Evidence: multi-agent excels breadth-first parallelizable + info exceeding single context; NOT for shared-context/high-dependency (most coding) or low-value (15x tokens). Source: multi-agent-research-system — pub 2025-06-13 — high — verification
- ReAct interleaves reasoning traces with actions (Wikipedia API), overcomes CoT hallucination on HotpotQA/Fever. Source: https://arxiv.org/abs/2210.03629 — Yao et al. via arXiv — pub 2022-10-06 (v3 2023-03-10) — high — pattern
- Reflexion reinforces via verbal feedback + episodic-memory buffer without weight updates, 91% pass@1 HumanEval vs GPT-4 80%. Source: https://arxiv.org/abs/2303.11366 — Shinn et al. — pub 2023-03-20 — high — pattern
- Plan-and-Solve (plan-and-execute primitive) devises plan then executes; on GPT-3 outperforms Zero-shot-CoT across 10 datasets, comparable to 8-shot CoT on math. Source: https://arxiv.org/abs/2305.04091 — Wang et al. — pub 2023-05-06 — high — pattern
- Tool-use loop: agent node → ToolNode → ToolMessage, repeats while tool_calls present, remaining_steps budget, checkpointer for durable state. Source: https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent — LangChain — current at access (deprecated → create_agent) — high — pattern
- Anthropic methodology steps: (1) evaluate immediately ~20 queries, (2) encode heuristics not rigid rules, (3) add observability/tracing, (4) durable/resumable execution, (5) scale effort to complexity. Source: multi-agent-research-system — pub 2025-06-13 — high

## Verdicts C5/C9/C10

- C5 ReAct 34%/10% ALFWorld/WebShop: UNVERIFIED (single publisher). Primary verbatim abstract. Hunt returned only recyclers (arXiv PDF/HTML, ICLR poster, OpenReview, Bytez, Google Research blog, study guides) — no independent reproduction.
- C9 Multi-agent 90.2% vs single-agent: UNVERIFIED (single publisher). Primary verbatim. Hunt: only recyclers (Simon Willison 2025-06-14, Substack, Medium) — no second publisher with own data. Internal eval by definition single-source.
- C10 Token 80% variance / 15x / 4x: UNVERIFIED (single publisher). Primary verbatim (tokens explain 80% of 95% explained; agents ~4x, multi-agent ~15x chat). No second source. BrowseComp is underlying benchmark but Anthropic analysis is own.
- (d) ReAct vs CoT hallucination: PATTERN-CORROBORATED, CLAIM-UNVERIFIED. LangGraph docs corroborate loop design (independent publisher), but no independent follow-up data verifying reduced hallucination retrieved.

## Could NOT find

- Independent reproduction of ReAct numbers; independent corroboration of 90.2%/80%/15x; post-Apr-2026 Anthropic update; ≤1-month LangGraph ReAct API (create_react_agent deprecated, successor not fetched).

## Leads

- OpenAI BrowseComp original page; Managed Agents docs https://platform.claude.com/docs/en/managed-agents/overview; LangChain create_agent successor + LangGraph plan-and-execute tutorial.
