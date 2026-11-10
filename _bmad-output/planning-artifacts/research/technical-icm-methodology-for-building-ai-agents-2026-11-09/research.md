---
title: 'technical research: ICM methodology for building AI agents'
type: 'technical'
topic: 'ICM methodology for building AI agents'
decision: 'Understand what ICM methodology for making AI agents is, its structure/steps, maturity, and how it is used in practice'
source: 'native-run'
status: complete
preset: 'standard'
validation: 'normal'
created: '2026-11-09'
updated: '2026-11-09'
verified: 17
disputed: 2
unverified: 6
---

# technical research: ICM methodology for building AI agents

**Decision this research serves:** Understand what ICM methodology for making AI agents is, its structure/steps, maturity, and how it is used in practice

## Executive summary

ICM is **Interpretable Context Methodology** — a 2026-vintage, filesystem-first approach to building AI agents where numbered folders are workflow stages and markdown files are prompts/context, instead of framework orchestration code [1][2][3]. The name is **verified by three independent publishers** ([1], [2], [3]) and the earlier spellings (Interpreted, Model Workspace Protocol / MWP) are explained as Feb–Mar 2026 renames [4][6]. No primary supports “Intelligent Context Methodology” [27]. ICM is **orthogonal in layer, peer in practice**: its paper frames it as prompt chaining instantiated as folders with MCP (Model Context Protocol) as a complementary layer [2], but independent summaries classify it as a filesystem alternative to frameworks [34], and its sub-agent delegation straddles both prompt chaining and orchestrator-workers [11]. A later LinkedIn explainer markets “Intelligent Context Management” with 60–80% token savings [36] — **unverified single-source promotional**, not a primary expansion.

**Maturity is thin but origin-anchored, not co-equal forks:** RinDig shows 21 commits Feb–Jul 2026 with 1.4k stars [1][6]; community variants explicitly defer to Jake (gist “adapted from RinDig” [4], Malkin “unofficial companion… Architect owns the method” [5]), establishing RinDig 5-layer as the origin/canonical dialect — with an MWP alias caveat (secondary guides still headline “Model Workspace Protocol” [34][35]). The independent NathanForster fork (44 commits, 5 stars) states “not yet deployed at organizational scale; pilot in progress” [3]; enterprise adoption remains NDA-limited and control-free [2]. The frameworks are dated-active — LangGraph [21], CrewAI (daily, partly docs-only [22]), OpenAI stable weekly to 2026-10-15 with substantive alphas to 2026-11-08 [23] — while AutoGen is now **archived** (Oct 2026 archival commits, redirect to Microsoft Agent Framework) [33][24], not merely stalled.

**Biggest caveat (post-Deepen):** Three structural descriptions coexist but anchor to one origin (RinDig 5-layer [1]; gist/Malkin defer to Jake [4][5]); no controlled token-savings comparison exists [2]. Performance claims are no longer merely unverified — ReAct 34/10 is **disputed** (independent reproduction: +5.2pp ALFWorld, worse on WebShop [31]); multi-agent 90.2%/80%/15× remains unverified as exact numbers [12] but is **directionally conditionalized** by an independent Google study (+80.9% parallelizable, −39 to −70% sequential [29][30]). Do not rest an adoption decision on any of them.

### Method note (preamble)

Native Run, breadth-first, 2 rounds (standard preset: 3 researchers per round, depth 2, validation normal, red-team off). Digests: 6 files under `digests/`. Harness `websearch` returned no results this run, so researchers used direct `webfetch` of primaries, DuckDuckGo HTML/lite [27], GitHub APIs, HN Algolia. Every claim traces to a source with publisher, date, access date (2026-11-09); status from the memlog ledger; confidence per-claim.

## 1. Landscape & maturity — what ICM is, and what surrounds it

### 1.1 ICM, defined

RinDig README gives the name verbatim as “Interpretable Context Methodology (ICM)” [1]. The arXiv record independently titles the method “Interpretable Context Methodology: Folder Structure as Agentic Architecture”, by Jake Van Clief and David McDermott (Eduba, University of Edinburgh), submitted 2026-03-17 (v1), revised 2026-03-18 (v2), 28 pages [2]. The independent NathanForster repo uses the same expansion [3]. **Status: verified** — three different publishers, same name.

The variants are explained, not contradictory: the LvlyBot gist expands ICM as “Interpreted Context Methodology” and attributes origin to RinDig/Interpreted-Context-Methodology [4]; RinDig commits (“Rename MWP to ICM”, “Fix spelling of Interpreted”, “Initial release: MWP framework”) show Interpreted/MWP were earlier labels replaced by Interpretable/ICM in Feb–Mar 2026 [6]. The paper abstract itself still calls the method “Model Workspace Protocol (MWP)” in its definition sentence while its title uses ICM [2]. **Status: verified** for the rename history. “Intelligent Context Methodology” was not evidenced in any primary; a DuckDuckGo lite search returned no results for it [27].

Trajectory: RinDig shows 21 commits from 2026-02-22 (“Initial release: MWP framework with 3 workspaces”) through 2026-07-25, with 1.4k stars, 255 forks, 42 watching at access [1][6]. The Jonathan Malkin field guide describes itself as an “unofficial community companion to Jake Van Clief and David McDermott’s ICM”, refreshed September 2026 [5]. This is emergence signal — community repos and a companion guide — not a standard.

Deepen resolution — canonical dialect [Deepen]: RinDig 5-layer is the origin/canonical dialect by authorship and community deferral, not by an explicit “canonical” sentence in the README. LvlyBot labels itself “adapted from RinDig” [4]; Malkin defers (“unofficial… Architect owns the method” [5]); red-team search surfaced RinDig `_core/CONVENTIONS.md` as the stated “canonical source” [1]. Caveat: MWP aliasing persists externally — Rayson headlines the paper as MWP [35], EmergentMind has an MWP topic page [34], and a practitioner blog splits “ICM is the method, MWP is the workspace pattern” [37]. No fork eclipses RinDig; no Jake-authored LinkedIn primary surfaced (only Garza’s mislabeled “Intelligent Context Management” explainer [36]).

### 1.2 The dominant approaches around it

LangGraph is a low-level orchestration runtime for long-running, stateful agents: workflows are graphs of shared state, nodes, and edges mixing deterministic and LLM-driven steps, and it does not prescribe prompts or a fixed agent architecture [8]. The OpenAI Agents SDK is positioned as a production-ready upgrade of the earlier Swarm experiment, built on Agents, Agents-as-tools / Handoffs, and Guardrails, with tracing, sessions, and human-in-the-loop [9]. CrewAI presents “Crews and Flows” (Agents, Tasks & Processes, Flows) as its core abstraction, with Enterprise deployment triggers and RBAC as a separate surface [10]. All three descriptions check out against primary docs; CrewAI confidence is medium, the other two high.

ICM’s own framing distinguishes layers: the RinDig README states MCP standardizes how models access external tools while ICM structures and delivers context — the two are complementary [1]. The exact phrase “ICM methodology” + AI agents returned zero DuckDuckGo hits while the broader ICM + AI agent query returned multiple repos/papers, consistent with a very recent, naming-unstable idea rather than an established pattern [27].

## 2. Architecture patterns in practice — structure, mapping, and steps

### 2.1 What ICM prescribes (three coexisting structures)

All variants converge on folders + markdown + explicit load decisions; they differ in layer count and file names. This coexistence is itself a finding:

- **RinDig (5 layers):** L0 CLAUDE.md (~800 tokens, always loaded); L1 CONTEXT.md (~300, read on entry); L2 Stage CONTEXT.md (~200–500, per-task); L3 Reference (selective); L4 Working artifacts (selective). Workspace shape: `workspace/CONTEXT.md`, `stages/01-research/CONTEXT.md` + `references/` + `output/`, `02-script`, `03-production`, plus `_config`, `shared`, `skills`, `setup/questionnaire.md` (script-to-animation: 3 stages; course-deck-production: 5; workspace-builder: 5) [1].
- **LvlyBot gist (3-layer routing):** L0 Root Router (CLAUDE.md) / L1 Workspace Router (workspace/CLAUDE.md) / L2 Stage Contract (stages/NN-name/CONTEXT.md), with “What to Load” and “What NOT to Load” exclusion tables; “The three-column load table (Resource | When | Why) is what makes ICM work” [4].
- **NathanForster (3-file trio + runner):** ICM.md (purpose, what to load, when to escalate), CONTEXT.md (routing map, allowed/forbidden actions), AGENT.md (inputs, outputs, definition of done), plus generic-agent-oriented / systems-engineering templates and an advanced stage-gated pipeline runner [3].

The paper’s stage contract is Inputs / Process / Outputs plus checkpoints (agent pauses for human steering) and audits (quality checklists); the Inputs table makes selection “explicit, editable, and auditable” [2]. Design principles quoted: “One stage, one job; Plain text as the interface; Layered context loading; Every output is an edit surface; Configure the factory, not the product”, via analogies to Unix pipeline design, modular decomposition, multi-pass compilation, literate programming, and the pipe-and-filter pattern [2].

Context budget claimed: “2,000 to 8,000 tokens” per stage versus monolithic “30,000 to 50,000”, with Threats to Validity admitting “No controlled comparison has been conducted” [2]. **Status: verified** that this is the claim; token savings remain unverified. Deben addendum [Deepen]: §5.4 frames scoping via Layer 3 (constraints) vs Layer 4 (inputs) and admits 5-layer generalization across model families is untested; §6.1 reframes ICM as multi-pass incremental compilation; §6.2–6.3 semantic debugging and edit-source tooling are future work, not shipped [2].

### 2.2 How ICM maps to dominant named patterns

The paper states “This implements prompt chaining at the filesystem level… ICM does the same thing, but the chain is a sequence of folders” — a peer of prompt chaining, not of ReAct [2]. **Status: verified for this line.** Deepen revision [Deepen]: orthogonality is weakened in practice — the paper positions ICM as a direct alternative to framework orchestration across 10 control surfaces (stage order, prompt edit, stage add, error recovery, branching, concurrency) [2], and independent summaries echo “alternative to code-heavy orchestration frameworks such as CrewAI, LangChain, and AutoGen” [34]. Orchestration is “one agent, reading the right files at the right moment, does the work that would otherwise require a multi-agent framework”, with sub-agents via “Opus 4.6 delegates to Claude Sonnet 4.6 through its Agent Teams capability” [2] — i.e., orchestrator-workers function with filesystem contracts. Anthropic treats chaining and orchestrator-workers as distinct workflows [11], so ICM straddles both; the clean orthogonality claim is revised to peer-in-practice. Gist worktree sub-agents [4] remain an adaptation variant.

The wider catalog, from primaries: Anthropic defines workflows as predefined code paths versus agents as LLMs dynamically directing their own processes/tool use, with the augmented LLM (LLM + retrieval/tools/memory) as base block, and five recurring workflows — prompt chaining, routing, parallelization, orchestrator-workers, and evaluator-optimizer [11]. ReAct interleaves reasoning traces with task-specific actions via a Wikipedia API [13]. Reflexion reinforces via verbal feedback with an episodic-memory buffer, reporting 91% pass@1 on HumanEval [14]. Plan-and-Solve (the plan-and-execute primitive) first divides a task into subtasks, then executes [15]. The tool-use loop is implemented as agent node → ToolNode → ToolMessage, repeating while `tool_calls` present, with a `remaining_steps` budget and checkpointer for durable state [16].

### 2.3 Methodology steps — and resolving the “frameworks” contradiction

Anthropic’s Dec 2024 guidance says start with LLM APIs directly, frameworks obscure prompts and invite premature complexity; add complexity only when it demonstrably improves outcomes [11]. Its Jun 2025 production account requires delegation budgets (simple 1 agent; comparisons 2–4; complex 10+), full production tracing, and durable execution (resume-from-checkpoint, retry, rainbow deployments) [12]. The Apr 2026 Managed Agents successor operationalizes this as session (durable append-only log outside the context window), harness (crash/reboot via getSession/emitEvent), and sandbox (replaceable via execute/provision) [17]. Anthropic’s own banner on the Dec 2024 page confirms that post is superseded by Managed Agents [11].

**Resolution:** simple holds for predictable workflows; framework patterns hold when tasks are long-horizon (exceeding context, hundreds of turns) or parallel-breadth, high-value, and heavy-tool. Multi-agent excels at breadth-first parallelizable queries and information exceeding a single context; it is not recommended for shared-context / high-dependency work (most coding) or low-value tasks (15× tokens) [12]. Anthropic’s step sequence in the production account is: think like agents via Console simulation; teach the orchestrator to delegate with objective/output/tool guidance/boundaries; scale effort to complexity; explicit tool-selection heuristics; start wide then narrow; guide thinking via extended/interleaved thinking; parallelize tool calls [12].

Original performance numbers (single-publisher): ReAct’s “34% and 10%” absolute gains on ALFWorld/WebShop [13], multi-agent’s 90.2% over single-agent Opus 4, token usage explaining ~80% of BrowseComp variance, and ~15× tokens (agents ~4×) [12]. Deepen verdicts [Deepen]: **ReAct 34/10 — disputed on magnitude**: an independent 2026 reproduction (6,268 trajectories) finds +5.2pp on ALFWorld and worse on WebShop (ReAct 35.6% vs Act 43.6%) [31]. **Multi-agent exact numbers — unverified as exact values**, but directionally conditionalized by an independent Google study (180/260-config): centralized +80.9% on parallelizable tasks; every multi-agent variant −39 to −70% on sequential PlanCraft [29][30]. ICM token claim remains unverified (no controlled test). Treat all exact numbers as hypotheses, not facts.

## 3. Implementation reality — what building agents actually costs

Production retrospectives converge on three burdens an ICM follower would contend with. Each is vendor-reported (single publisher each); numbers are flagged accordingly.

**Cache-first economics.** Viktor reports a 40-step Claude Opus 4.8 thread re-sending ~2.17M input tokens while the transcript is ~85K: $11.35 without prompt caching versus $2.07 with caching (81.8% cut), plus time-to-first-token savings from skipping prefill [18]. **Status: verified as reported** by Viktor (Round 1 numbers matched exactly); **medium** as a generalizable claim (single-vendor retrospective — do not treat as vendor-neutral truth).

**Evals gap.** For a team building synthetic-user agents for ~1 year, building a massive eval set became prohibitively costly — evaluation itself was ~5× costlier than running the agent (many narrow intents), and long-running sessions never became reliable enough [19]. Financial-agent teams report offline evals cannot cover every question/session and an LLM judge is too expensive beyond a small slice, so they added a no-LLM-judge release-health checker on tool calls/retries/loops/output length/cost per turn over all production traces [20]. **Status: unverified** (single publisher each; no 6–12-month post-mortem with independent verification found).

**Tool/context sprawl.** Tool schemas alone blow past context and tool-picking accuracy degrades as the toolset grows; at 9,800+ integrations Viktor replaced per-tool schemas with real-code SDK calls to keep the prompt prefix small and byte-stable for caching [18]. Prompt caches are fragile — exact prefix match plus ~5-minute Anthropic TTL — so cache-friendly agents forbid timestamps in the system prompt, treat threads as append-only logs, and time compaction (hot ~50K / idle ~16K; delayed compaction at 3 minutes inside the 5-minute TTL) around cache death, with per-provider adapters [18].

Implication: any ICM guide omitting cache-first architecture, cheap over-all-traces monitoring (not just sampled evals), and tool-discovery discipline diverges from production reality. No ICM implementation guide with steps, roles, or eval evidence was found beyond the primaries in §2.1; ICM’s claimed 2K–8K token budget [2] does not by itself address quadratic re-send cost, eval sampling gaps, or dynamic tool discovery.

Adversarial note: a single-author Flakestorm post reports 5.2% robustness (57/60 failures), 0% encoding-attack and prompt-injection pass rates, ~30s latency under stress [25]. No second independent publisher was found; repo API confirms the tool exists, not the figure [25]. **Status: unverified** — one post-mortem is an anecdote; do not cite as a pattern.

## 4. Ecosystem health — who is alive, dated, and risky

Dated trajectories (5-release windows, not snapshots):

- **LangGraph: active.** Latest stable `langgraph==1.2.20` published 2026-11-05, SDK/CLI patches 2026-11-06 — within 4 days of access [21]. The tool-use docs for `create_react_agent` are deprecated in favor of `create_agent` [16] — a version-level churn signal: do not treat the old API as current.
- **CrewAI: active.** Latest 1.15.39 published 2026-11-09 (access date), with daily patches 2026-11-03 through 2026-11-09 and no breaking change noted in the window [22]. The docs’ Enterprise surface [10] is separate from OSS cadence — roadmap drive (OSS vs Enterprise) was not resolved this run.
- **OpenAI Agents SDK: stable weekly through Oct 2026, substantive alpha track in Nov.** Stable: v0.22.0 2026-08-19 through v0.24.0 2026-10-15; pre-release alphas 2026-11-03 and 2026-11-08 [23]. v0.24.0 carries compatibility changes (custom error callbacks, saved-graph schema 1.18→1.21); no `BREAKING` label — migration/compat framing; alpha breaking items (Runner→Agent.run, `ManualTool` removal, strict `write_policy`) are pre-release, not stable [23]. Latest stable is 25 days before access; November activity is alpha-only.
- **AutoGen: archived.** Last release `python-v0.7.5` dated 2025-09-30 (13 months before access); Oct 2026 pushes are archival docs (“add archive caution” 2026-10-28, “archived and no longer maintained” 2026-10-16) [33]; Apr 2026 maintenance banner already redirected new users to Microsoft Agent Framework [33]. Repo still shows `archived:false` with 61,762 stars [24], but intent is explicit archival, not revival. Treat as archived; do not adopt.
- **OTel GenAI:** the `gen_ai.*` semantic conventions have moved out of opentelemetry.io to `github.com/open-telemetry/semantic-conventions-genai`; the legacy page is now only a moved-notice with no version [26]. Schema version/compatibility **unverified** — do not claim a version from the notice.

ICM community signals, separately from frameworks: RinDig 1.4k stars / 255 forks [1][6] versus NathanForster 5 stars / 0 forks with an explicit early-stage disclaimer [3], Malkin 8 stars / 3 forks [5]. Paper-reported external adoption (Edinburgh Neuropolitics Lab, ICR Research, Academy of International Affairs Bonn) is NDA-limited (“details limited by nondisclosure agreements, but their existence is noted”); the base is a 52-member invite-only community with 30/33 U-shaped intervention self-reported via conversation, not instrumented [2]. **Status: unverified** for adoption scale. Aggregator wiki enterprise numbers (Pacific Life >1,500 employees, 95% adoption, IAG/KPMG/Colgate) are single-publisher with no second independent source [7]. **Status: unverified** — do not treat as verified performance.

Legacy LangGraph GitHub Pages (`langchain-ai.github.io`) now redirects to `docs.langchain.com`, consistent with docs consolidation [28]. The LangChain harness/framework/runtime/platform taxonomy split (Deep Agents / LangChain / LangGraph / LangSmith) [8] is **revised [Deepen]**: the strict-trichotomy reading is disputed. OpenAI self-labels its SDK a “harness” [32]; CrewAI self-labels “framework” (while also using “runtime” loosely) — labels are converging cross-publisher, not LangChain-only, but the strict three-way split is not independently adopted. LangGraph freshness cited as “stable 2026-11-05” is substantive, not churn (per-interrupt IDs, strict resume mode, same-day SDK/CLI) [21]; CrewAI “daily” is partly docs/test-only (1.15.39, 1.15.35) with real features in the same window [22] — cadence artifact is partial.

**Five-year regret read (bounded):** The frameworks with dated, within-the-week activity (LangGraph, CrewAI, OpenAI alpha) carry lower near-term regret risk than the release-stalled AutoGen [24] or the Feb–Aug 2026 ICM forks with no organization-scale deployment [3]. ICM’s own Threats to Validity (no controlled comparison) [2] and Malkin’s “unofficial companion” status [5] bound any longevity claim. No repo time-series beyond 5-release windows was collected — deeper vitality (release cadence over 2+ years, contributor churn) remains unassessed.

## 5. Cross-dimension insights

- **ICM’s value is context architecture, not agent reasoning.** It overlaps Anthropic’s “start simple, show planning steps, craft the agent-computer interface” principles [11][2] but substitutes filesystem state for code-delegated parallel subagents [12]. The combination only works where tasks are sequential and file-shaped (script → animation, course-deck, workspace-builder [1]); Anthropic’s production guidance says sequential/parallel trade-offs invert for shared-context work like coding [12].
- **ICM’s token claim collides with cache economics.** ICM budgets 2K–8K tokens per stage [2]; Viktor’s burden is quadratic re-send (~2.17M tokens on an 85K transcript) and 5-minute TTL fragility [18]. A filesystem design without append-only, cache-stable prefixes and compaction timing will pay Viktor’s $11.35 cost class regardless of per-stage budget.
- **Freshness splits the decision.** ICM primaries are 3–8 months old (paper Mar 2026 [2], gist Apr 2026 [4], RinDig Jul 2026 [1], Nathan Aug 2026 [3]) — inside the AI landscape bar — while the “dominant patterns” literature it contradicts is Dec 2024 [11] superseded by Apr 2026 [17]. Citing Dec 2024 for current landscape would fail the ≤3-month AI bar; citing only pattern definitions is defensible (≤2 years).

Three insights stand; there is no fourth.

## 6. Contrary evidence

Red-team pass (Deepen): **on** for this slice — two fresh-context skeptics attacked the two major conclusions with search budget but no supporting evidence. Both verdicts: **weakened** (neither overturned). Whatever survived gets its strongest counter-argument acknowledged below; weakened conclusions are already revised in §§1–4 and §7.

**C-A ICM canonical/orthogonal — weakened.** Core authorship (Van Clief/McDermott) and 5-layer description survive; the strong “canonical / orthogonal / not a peer” reading does not. Strongest counters: the paper positions ICM as a direct replacement/alternative to framework orchestration on 10 control surfaces [2], independently echoed as “alternative to code-heavy orchestration frameworks such as CrewAI, LangChain, and AutoGen” [34]; Critters canonical name is unstable (v1 MWP → v2 ICM [2]; Rayson “also referred to as MWP” [35]; method/workspace split [37]); delegation (Opus→Sonnet via Agent Teams [2]) plus Anthropic’s distinct chaining vs orchestrator-workers boundary [11] straddle the orthogonality claim; no independent measurement supports the prompt-chaining effectiveness claim, and the paper’s own threats (N=52 invite-only, single family, no controlled comparison) [2] plus Table 1 MCP overlap (“Local scripts or MCP connections” [2]) qualify the “different layer” story. Searched but not found: no head-to-head ICM vs ReAct benchmark, no alternative expansion, no fork eclipse.

**C-B Ecosystem status — weakened (ordering survives, glosses revised).** LangGraph/CrewAI fresher-stable ordering and AutoGen staleness survive; “daily fresh” and “single-publisher taxonomy” glosses fail on primary evidence. Strongest counters: OpenAI self-labels “harness” [32] and CrewAI self-labels “framework,” refuting LangChain-only language for those terms; OpenAI November alphas ship substantive breaking/native-runtime features [23], so “active-but-alpha” understates November activity if freshness means commits; CrewAI “daily” includes docs/test-only releases [22] (partial artifact; real features in the same window stand). Found to support (not refute): LangGraph 1.2.20 substantive same-day features [21]; AutoGen README “archived and no longer maintained,” Microsoft Agent Framework as distinct successor, no post-Sep-2025 releases [24][33]. Searched but not found: no AutoGen revival code, no pre-Oct-2026-10-15 stabler-than-v0.24.0 OpenAI stable.

## 7. Recommendations

Each recommendation is bound to the decision (understand whether/how to use ICM for making agents) with its confidence basis. Low-confidence/disputed bases say so in the same sentence.

1. **Adopt ICM as a context-delivery layer, not as an agent framework.** Follow RinDig L0–L4 or the paper’s Inputs/Process/Outputs contract [1][2] for sequential, file-shaped pipelines; keep ReAct / orchestrator-workers / planner-execute as the reasoning layer [12][13]. Basis: verified mapping [2]; confidence **high** for the distinction.
2. **Pin the RinDig 5-layer dialect as canonical.** Origin is RinDig [1] with community deferral (gist “adapted from RinDig” [4], Malkin “Architect owns the method” [5]); treat gist/Malkin/Nathan variants as adaptations, not co-equals, and record the choice in CONTEXT.md. Basis: verified origin-with-deferral; confidence **high** that pinning to origin beats floating.
3. **Do not budget on unverified or disputed numbers.** Do not cite ReAct 34/10 [13] (disputed by [31]), multi-agent 90.2%/80%/15× [12] (exact values unverified, direction conditionalized by Google [29][30]), ICM token savings [2], or Eduba scale [7] as cost justification — each rests on a low-confidence, unverified, or disputed claim in this run. Basis: ledger statuses; confidence **high** that withholding is correct.
4. **Hold the frameworks to dated evidence.** For runtime choice, prefer LangGraph (stable 2026-11-05, substantive [21]) or CrewAI (daily through 2026-11-09, partly docs-only [22]) over OpenAI stable (v0.24.0 2026-10-15; Nov activity alpha-only [23]); do not adopt AutoGen — archived (Oct 2026 archival commits; MAF successor) [33][24]. Basis: verified dated trajectories + archival commits; confidence **high** except OpenAI alpha substance (**medium**).
5. **Design ICM workspaces cache-first.** Append-only, timestamp-free prefixes; per-provider adapters; compaction timed around the 5-minute TTL; SDK-code tools instead of prompt schemas at scale [18]. Basis: verified as Viktor-reported, single-vendor — confidence **medium** (generalizability unproven); interleaving this with ICM load tables is the implementation test.
6. **Gate expansion on independent eval.** Replicate Anthropic’s “~20 real queries + LLM-as-judge rubric + human testing” [12] and Tessary/Oimo-style all-traces release-health checks [19][20] before scaling beyond a pilot — noting those eval-cost figures are themselves unverified [19][20]. Basis: high-confidence method, unverified cost numbers; confidence **medium** overall.

## 8. Open questions

- **Which ICM dialect is canonical?** [Deepen resolved]: RinDig 5-layer is origin/canonical by authorship + community deferral [1][4][5]; remaining: fetch `icm-architect` repo to confirm the maintained build skill vs older repo, and Nathan primary to verify it is also an adaptation.
- **Do ICM stage budgets (2K–8K) beat monolithic context (30K–50K) in a controlled test?** [Deepen: still open] No controlled test found in paper §4–6 or independently [2]; would take a same-model/same-task benchmark with cache held constant.
- **Are ReAct 34/10 [13] and multi-agent 90.2%/80%/15× [12] reproducible?** [Deepen: ReAct disputed [31]; multi-agent exact unverified, direction conditionalized [29][30]] Remaining: Google paper PDF token table + BrowseComp baselines for the 15× test.
- **Is OpenAI Agents SDK stable cadence and breaking-change surface safe?** [Deepen resolved]: weekly stable Aug–Oct; v0.24.0 compat changes; no `BREAKING` label; alpha breaking is pre-release [23]. Remaining: full CHANGELOG/MIGRATION scan + PyPI second publisher.
- **Is AutoGen’s Oct 2026 push revival or archival?** [Deepen resolved]: archival — “archived and no longer maintained” commits, MAF redirect [33][24].
- **Red-team / bear case:** [Run in Deepen, §6] Both major conclusions weakened, neither overturned. Remaining: independent ICM vs ReAct/orchestrator benchmark (searched, not found) would be the next overturn test.
- **Unfetched primaries:** rayson.dev pillars (fetched via Rayson pillar [35]; direct fetch blocked), Nathan primary, LinkedIn/video transcripts — remaining direct-fetch candidates.

## 9. Source appendix

| [n] | Claim it supports | Publisher | Pub date | Accessed | Confidence |
| --- | --- | --- | --- | --- | --- |
| [1] | ICM = Interpretable Context Methodology; 5-layer hierarchy; workspace shape; MCP complementary | [RinDig / Jake Van Clief](https://github.com/RinDig/Interpretable-Context-Methodology) | 2026-07-25 | 2026-11-09 | high |
| [2] | Title/authors Mar 2026; stage contract; prompt-chaining mapping; MCP orthogonal; 2K–8K budget; NDA adoption | [arXiv / Van Clief & McDermott](https://arxiv.org/abs/2603.16021) | 2026-03-18 | 2026-11-09 | high |
| [3] | Same expansion; 3-file trio; early-stage disclaimer, pilot in progress | [NathanForster](https://github.com/NathanForster/ICM) | 2026-08-01 | 2026-11-09 | high |
| [4] | Interpreted variant; 3-layer routing; load tables; orchestrator worktree | [LvlyBot (gist)](https://gist.github.com/LvlyBot/91c386584c99e4f3c300013d5cebaf78) | 2026-04-16 | 2026-11-09 | high |
| [5] | Unofficial Malkin field guide, Sep 2026, 8 stars | [Jonathan Malkin](https://github.com/jonathanmalkin/icm-field-guide) | 2026-09-01 | 2026-11-09 | high |
| [6] | MWP→ICM rename; 21 commits Feb–Jul 2026; 1.4k stars | [RinDig commits (GitHub)](https://github.com/RinDig/Interpretable-Context-Methodology/commits/main/) | 2026-03-08 | 2026-11-09 | high |
| [7] | Eduba enterprise numbers (aggregator) | [Learn AI / Miraheze wiki](https://ai.miraheze.org/wiki/Interpretable_Context_Methodology) | 2026-09-10 | 2026-11-09 | low (unverified) |
| [8] | LangGraph runtime: graphs of shared state/nodes/edges; harness/framework/runtime split | [LangChain docs](https://docs.langchain.com/oss/python/langgraph/overview) | undated | 2026-11-09 | high |
| [9] | OpenAI SDK: Swarm upgrade; Agents+Handoffs+Guardrails; Responses API default | [OpenAI Agents docs](https://openai.github.io/openai-agents-python/) | undated | 2026-11-09 | high |
| [10] | CrewAI Crews/Flows core; Enterprise surface | [CrewAI docs](https://docs.crewai.com/) | undated | 2026-11-09 | medium |
| [11] | Five workflows; start simple; ACI principles; superseded by Managed Agents | [Anthropic](https://www.anthropic.com/research/building-effective-agents) | 2024-12-19 | 2026-11-09 | high (stale for landscape) |
| [12] | Orchestrator-workers; 90.2%; token 80%/15×; delegation budgets; eval method | [Anthropic Research](https://www.anthropic.com/research/multi-agent-research-system) | 2025-06-13 | 2026-11-09 | high (numbers unverified) |
| [13] | ReAct interleaves reasoning/actions; 34%/10% gains | [Yao et al. via arXiv](https://arxiv.org/abs/2210.03629) | 2023-03-10 | 2026-11-09 | high (numbers unverified) |
| [14] | Reflexion 91% HumanEval | [Shinn et al. via arXiv](https://arxiv.org/abs/2303.11366) | 2023-03-20 | 2026-11-09 | high |
| [15] | Plan-and-Solve plan-and-execute | [Wang et al. via arXiv](https://arxiv.org/abs/2305.04091) | 2023-05-06 | 2026-11-09 | high |
| [16] | ToolNode loop; create_react_agent deprecated | [LangGraph reference](https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent) | undated | 2026-11-09 | high |
| [17] | Managed Agents session/harness/sandbox | [Anthropic Engineering](https://www.anthropic.com/engineering/managed-agents) | 2026-04-08 | 2026-11-09 | high |
| [18] | Caching $11.35 vs $2.07; 9,800 tools; TTL fragility | [Viktor](https://viktor.com/research/how-we-built-viktor-around-prompt-caching/md) | 2026-06 | 2026-11-09 | medium (single-vendor) |
| [19] | Evals 5× costlier than agent run | [HN / Tessary](https://hn.algolia.com/api/v1/search?query=AI%20agents%20evals%20production&tags=story&hitsPerPage=5) | 2026-10-10 | 2026-11-09 | medium (unverified) |
| [20] | Release-health checker all traces | [Oimo AI / GitHub](https://github.com/oimo-ai/apgar) | 2026-10-29 | 2026-11-09 | medium (unverified) |
| [21] | LangGraph 1.2.20 dated 2026-11-05 | [LangChain AI (GitHub API)](https://api.github.com/repos/langchain-ai/langgraph/releases) | 2026-11-06 | 2026-11-09 | high |
| [22] | CrewAI 1.15.39 dated 2026-11-09 | [CrewAI (GitHub API)](https://api.github.com/repos/crewAIInc/crewAI/releases) | 2026-11-09 | 2026-11-09 | high |
| [23] | OpenAI alpha v0.24.0 dated 2026-11-08 | [OpenAI (GitHub API)](https://api.github.com/repos/openai/openai-agents-python/releases) | 2026-11-08 | 2026-11-09 | medium |
| [24] | AutoGen last release 2025-09-30; push 2026-10-28 | [Microsoft (GitHub API)](https://api.github.com/repos/microsoft/autogen/releases) | 2025-09-30 | 2026-11-09 | high |
| [25] | Flakestorm 5.2% / 0% / ~30s (single-author) | [HN 46587182 / flakestorm](https://hn.algolia.com/api/v1/search?query=Flakestorm) | 2026-01-12 | 2026-11-09 | low (unverified) |
| [26] | OTel gen_ai.* moved, no version | [OpenTelemetry](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | undated | 2026-11-09 | medium |
| [27] | Exact-phrase zero hits; Intelligent null; naming unstable | [DuckDuckGo search pages](https://duckduckgo.com/html/?q=ICM+AI+agent+framework) | 2026-11-09 | 2026-11-09 | medium |
| [28] | Docs redirect = consolidation | [LangChain (GitHub Pages redirect)](https://langchain-ai.github.io/langgraph/) | undated | 2026-11-09 | medium |
| [29] | Multi-agent +80.9% parallelizable; −39 to −70% sequential (Google study) | [Google Research blog](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/) | 2026-01-28 | 2026-11-09 | high |
| [30] | Same study (paper): 260 configs, R²=0.513, 17.2× error amplification | [arXiv / Kim & Liu](https://arxiv.org/abs/2512.08296) | 2025-12 | 2026-11-09 | high |
| [31] | ReAct reproduction: ALFWorld +5.2pp; WebShop worse (−8.0pp) | [GeYugong (GitHub)](https://github.com/GeYugong/react-reproduction) | 2026 | 2026-11-09 | medium (sole reproducer) |
| [32] | OpenAI “harness” self-label (agents SDK evolution) | [OpenAI](https://openai.com/index/the-next-evolution-of-the-agents-sdk) | 2026-04-15 | 2026-11-09 | high |
| [33] | AutoGen archival commits Oct 2026; MAF redirect Apr 2026 | [Microsoft (GitHub commits)](https://api.github.com/repos/microsoft/autogen/commits?per_page=15) | 2026-10-28 | 2026-11-09 | high |
| [34] | MWP as alternative to CrewAI/LangChain/AutoGen (peer framing) | [EmergentMind](https://www.emergentmind.com/topics/model-workspace-protocol-mwp) | 2026-07-04 | 2026-11-09 | high |
| [35] | Rayson pillar: MWP alias; 5-layer echo | [Rayson.Dev](https://rayson.dev/blog/pillar-icm-complete-guide) | 2026-05-31 | 2026-11-09 | medium |
| [36] | LinkedIn “Intelligent” mislabel; 60–80% savings promo | [LinkedIn / Mark Garza](https://www.linkedin.com/posts/markgarza_in-this-video-i-break-down-icm-intelligent-activity-7475637054568333312-aHak) | 2026-07 | 2026-11-09 | low (unverified) |
| [37] | “ICM is method, MWP is workspace pattern” split | [Johnny Leuthard (blog)](https://johnnyleuthard.7h474hh9j9.workers.dev/blog/most-ai-problems-do-not-start-with-the-model) | 2026-07-13 | 2026-11-09 | medium |

Citations check is mechanical (inline `[n]` vs this table). Every inline marker above resolves to a row; rows with no marker would be orphaned. No fenced code blocks contain citation markers.

## 10. Staleness map

Computed via `recon_kit staleness` (machine-derived) at access 2026-11-09, 29 ledger claims (22 initial + 7 Deepen). Windows: icm-definition/structure/mapping/maturity 6 mo; pattern 24 mo; methodology-steps 3 mo; performance 3 mo; ops-burden 6 mo; retrospective 12 mo; ecosystem 6 mo; taxonomy 6 mo; version 1 mo. Stale = `pub + window` < access date.

**Stale: 14 of 29.** **Earliest re-check: 2023-06-01** (ReAct performance [13], pub 2023-03 + 3 mo). Full re-check dates:

- Fresh (15): Anthropic five workflows pattern [11] → 2026-12-01; Viktor caching [18] → 2026-12-01; Tessary evals [19] → 2027-10-01; Oimo release health [20] → 2027-04-01; LangGraph [21], CrewAI [22], OpenAI alpha [23] → 2027-05-01; Nathan early-stage [3] → 2027-02-01; Wiki Eduba [7] → 2027-03-01; RinDig name [1] → 2027-01-01; Nathan name [3] → 2027-02-01; ReAct reproduction [31] → 2028-01-01; AutoGen archival [33] → 2027-04-01; peer framing [34], LinkedIn promo [36] → 2027-01-01.
- Stale (14): ICM arXiv name [2], MWP commits [6], stage contract [2], prompt-chaining mapping [2], NDA adoption [2] — all pub 2026-03 → re-check 2026-09-01 (past); gist rename [4] pub 2026-04 → 2026-10-01 (past); Managed Agents [17] pub 2026-04 → 2026-07-01 (past); ReAct gains [13] → 2023-06-01; multi-agent numbers [12] pub 2025-06 → 2025-09-01; AutoGen [24] pub 2025-09 → 2026-03-01; Flakestorm [25] → 2026-04-01; Google conditional [29] pub 2026-01 → 2026-04-01; OpenAI harness [32] pub 2026-04 → 2026-10-01; OpenAI version cadence [23] pub 2026-10 → 2026-11-01 (version 1-mo window).

Note: Anthropic Dec 2024 guidance [11] as a *pattern* (24-mo window) is fresh to 2026-12-01 but stale as *landscape* (AI ≤3 mo) and is superseded by Managed Agents [17]. Deepen added 3 stale / 4 fresh. Refresh order: re-verify stale March/April 2026 ICM primaries ([1][2][6]) and Managed Agents [17] first, OpenAI version monthly, and the performance numbers only when a second publisher appears.
