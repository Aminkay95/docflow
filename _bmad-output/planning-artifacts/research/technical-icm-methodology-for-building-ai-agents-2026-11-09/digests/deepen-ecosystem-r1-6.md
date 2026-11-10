# Ecosystem resolver digest (Deepen)

## OpenAI Agents SDK stable cadence
- Stable: v0.21.1 2026-08-16, v0.22.0 2026-08-19, v0.22.1 2026-09-08, v0.22.2 2026-09-09, v0.22.3 2026-09-17, v0.23.0 2026-10-02, v0.23.1 2026-10-02, v0.24.0 2026-10-15; alphas 2026-11-03/08. Weekly Aug→Oct, 25-day stable gap at 2026-11-09. — https://api.github.com/repos/openai/openai-agents-python/releases?per_page=10 — OpenAI GitHub — pub 2026-10-15 — high — version
- v0.24.0 compat changes (custom error callbacks kind=tool_exception, saved graphs schema 1.18→1.21, some legacy graphs rejected). v0.23.1 compatible/patch vs 0.23.0 but carries 0.23.0 migration (strict tools, approvals, encrypted history). v0.22.0 tightens contract (move org/project to AsyncOpenAI). No BREAKING label in stable bodies; language is compatibility/migration. — same — medium-high
- Alpha breaking (pre-release, not stable): Runner aliases → Agent.run, Turn/Session wait→get_result, Session.interrupt→cancel, ManualTool removed; alpha.7 write_policy strict default, MCP approval_mode change. — same (alpha bodies) — high

## AutoGen ARCHIVED
- 2026-10-28 'docs: add archive caution to landing page (#8393)' d07c667; 2026-10-16 'docs: clarify AutoGen is archived and no longer maintained (#8366)' 004749e. — https://api.github.com/repos/microsoft/autogen/commits?per_page=15 — Microsoft GitHub — pub 2026-10-28/16 — high — ecosystem
- 2026-04-06 'Update maintenance mode banner in readme (#7521): Updating maintenance banner and redirect new users to Microsoft Agent Framework.' — same — high. Trajectory: site/docs 2025-10-02 → banner Apr 2026 → archived Oct 2026. No Oct 2026 feature commits; Oct pushes docs-only.
- Last version bump 0.7.5 on 2025-09-30 (83afbf5/a2bf539). — same — medium-high

## Taxonomy NOT corroborated (LangChain-only language in strict form)
- LangChain: Runtime=LangGraph (durable execution), Framework=LangChain (abstractions), Harness=Deep Agents; table lists CrewAI + OpenAI under Framework (LangChain classifying competitors, not their self-labels). — https://docs.langchain.com/oss/python/concepts/products.md + /langgraph/overview — LangChain — live docs — high — taxonomy
- CrewAI: 'core runtime' (Quickstart) + 'framework manages state.messages' (Conversational Flows) — interchangeable, not strict split. — DDG site:docs.crewai.com framework runtime snippets (v1.15.x) — medium (snippet-level)
- OpenAI: 'agent is an LLM configured with instructions, tools, and optional runtime behavior such as handoffs, guardrails' — per-agent config, no trichotomy. — DDG site:openai.github.io snippet (agents/) — medium (snippet-level)

## Not found
- No BREAKING labels in stable bodies (CHANGELOG unchecked).
- No CrewAI/OpenAI adoption of strict trichotomy in retrieved snippets (negative from snippets ≠ proof of absence).
- No AutoGen revival in 15-commit window; older history not paged.
- Prior LangGraph/CrewAI freshness not re-fetched here (treated as prior-run established).
