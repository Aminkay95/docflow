# ICM Primaries Deep Dive — Digest (R2)

Accessed: 2026-11-09. Sources read: RinDig GitHub, NathanForster GitHub + raw CHANGELOG, arXiv abs + HTML, LvlyBot gist, jonathanmalkin field-guide GitHub, RinDig commits, Miraheze wiki, DuckDuckGo HTML/lite.

## ICM-definition

- "RinDig README gives ICM as 'Interpretable Context Methodology (ICM)'." — https://github.com/RinDig/Interpretable-Context-Methodology — RinDig / Jake Van Clief — pub 2026-07-25 — confidence high
- "arXiv title 'Interpretable Context Methodology: Folder Structure as Agentic Architecture', authors Jake Van Clief, David McDermott, submitted 2026-03-17 v1 revised 2026-03-18 v2." — https://arxiv.org/abs/2603.16021 — arXiv — pub 2026-03-18 — confidence high
- "NathanForster repo also expands ICM as 'Interpretable Context Methodology'." — https://github.com/NathanForster/ICM — NathanForster — pub 2026-08-01 — confidence high
- "LvlyBot gist expands ICM as 'Interpreted Context Methodology' and attributes origin to Jake Van Clief in RinDig/Interpreted-Context-Methodology." — https://gist.github.com/LvlyBot/91c386584c99e4f3c300013d5cebaf78 — LvlyBot — pub 2026-04-16 — confidence high
- "RinDig commits explain variant: 'Rename MWP to ICM', 'Fix spelling of Interpreted', 'Initial release: MWP framework' — Interpreted/MWP earlier labels replaced by Interpretable/ICM Feb-Mar 2026." — https://github.com/RinDig/Interpretable-Context-Methodology/commits/main/ — GitHub / RinDig — pub 2026-03-08 — confidence high
- "Paper abstract calls method 'Model Workspace Protocol (MWP)' while title uses ICM, evidencing MWP→ICM rename." — https://arxiv.org/html/2603.16021v2 — arXiv — pub 2026-03-18 — confidence medium
- "'Intelligent Context Methodology' NOT evidenced in primaries; lite search 'No results found'." — https://lite.duckduckgo.com/lite/?q=%22Intelligent+Context+Methodology%22+ICM — DuckDuckGo Lite — accessed 2026-11-09 — confidence medium

## Authors/dates/versions + maturity

- "RinDig README: 'Created by Jake Van Clief'." — same RinDig — pub 2026-07-25 — high
- "arXiv: Jake Van Clief and David McDermott, Eduba, University of Edinburgh, Palm Coast, Florida, USA, 28 pages, 5 figures, 2 tables, 54 refs, v1 2026-03-17 v2 2026-03-18, MIT/CC-BY." — https://arxiv.org/abs/2603.16021 — pub 2026-03-18 — high
- "RinDig trajectory: 21 commits 2026-02-22 'Initial release: MWP framework with 3 workspaces' through 2026-07-25; 1.4k stars, 255 forks, 42 watching at access." — commits URL — pub 2026-07-25 — high
- "NathanForster: 44 commits, CHANGELOG 2026-06 to 2026-08 (dry-run validation, runner fixes); 5 stars, 0 forks; README 'early-stage... not yet deployed at organizational scale; pilot in progress'." — https://raw.githubusercontent.com/NathanForster/ICM/master/CHANGELOG.md — NathanForster — pub 2026-08-01 — high
- "Field guide: Jonathan Malkin 'unofficial community companion to Jake Van Clief and David McDermott's ICM', refreshed Sep 2026, 8 stars, 3 forks, 2 commits; implementation via separate ICM Architect." — https://github.com/jonathanmalkin/icm-field-guide — Jonathan Malkin — pub 2026-09-01 — high

## Structure (verbatim)

- "RinDig five-layer hierarchy: L0 CLAUDE.md Always loaded (~800 tokens); L1 CONTEXT.md Read on entry (~300); L2 Stage CONTEXT.md Read per-task (~200-500); L3 Reference selective; L4 Working artifacts selective." — https://github.com/RinDig/Interpretable-Context-Methodology — pub 2026-07-25 — high
- "Workspace shape: workspace/CONTEXT.md; stages/01-research/CONTEXT.md + references/ + output/; 02-script; 03-production; _config; shared; skills; setup/questionnaire.md (script-to-animation 3 stages, course-deck 5, workspace-builder 5)." — same — high
- "Stage contract = 'Inputs / Process / Outputs' plus 'checkpoints (agent pauses for human steering) and audits (quality checklists)'; Inputs table makes selection 'explicit, editable, and auditable'." — https://arxiv.org/html/2603.16021v2 — pub 2026-03-18 — high
- "Gist 3-layer routing L0 Root Router (CLAUDE.md)/L1 Workspace Router (workspace/CLAUDE.md)/L2 Stage Contract (stages/NN-name/CONTEXT.md) with 'What to Load' + 'What NOT to Load'; 'three-column load table (Resource | When | Why) is what makes ICM work'." — https://gist.github.com/LvlyBot/91c386584c99e4f3c300013d5cebaf78 — pub 2026-04-16 — high
- "NathanForster: three core files 'ICM.md (purpose, what to load, when to escalate), CONTEXT.md (routing map, allowed/forbidden), AGENT.md (inputs, outputs, definition of done)' + Templates/generic-agent-oriented-ICM, systems-engineering-ICM, advanced-options." — https://github.com/NathanForster/ICM — pub 2026-08-01 — high
- "Paper analogies: 'Unix pipeline design, modular decomposition, multi-pass compilation, literate programming' / 'pipe-and-filter'; principles = 'One stage, one job; Plain text as the interface; Layered context loading; Every output is an edit surface; Configure the factory, not the product'." — https://arxiv.org/html/2603.16021v2 — pub 2026-03-18 — high
- "Context budget: '2,000 to 8,000 tokens' per stage vs monolithic '30,000 to 50,000'; no controlled comparison — Threats to Validity admits 'No controlled comparison has been conducted'." — same — high

## Mapping

- "'This implements prompt chaining at the filesystem level. Wu, Terry, and Cai introduced AI Chains... ICM does the same thing, but the chain is a sequence of folders' — peer of prompt chaining, filesystem instantiation, not ReAct." — https://arxiv.org/html/2603.16021v2 — high
- "Orchestration: 'one agent, reading the right files at the right moment, does the work that would otherwise require a multi-agent framework'; sub-agents via 'Opus 4.6 delegates to Claude Sonnet 4.6 through its Agent Teams capability' — orchestrator-workers with filesystem contracts." — same — high
- "MCP distinction: 'MCP standardizes how models access external tools... ICM addresses a different layer: how to structure and deliver context... complementary' — orthogonal layer." — https://github.com/RinDig/Interpretable-Context-Methodology — high
- "Gist orchestrator: 'Stage 01 (Orchestrator) spawns sub-agents... in isolated git worktree... collects results' — orchestrator-workers from adaptation." — gist — medium

## Maturity & contradictions

- "External adoption claimed: Edinburgh Neuropolitics Lab, ICR Research, Academy of International Affairs Bonn, but 'details limited by NDAs, but existence noted'; base = 52-member invite-only community, 30/33 U-shaped self-reported via conversation (not instrumented)." — https://arxiv.org/html/2603.16021v2 — medium
- "Nathan maturity contradicts high-adoption: 'not yet deployed at organizational scale; pilot in progress' — early-stage, forked-lineage." — https://github.com/NathanForster/ICM — high
- "Wiki (aggregator) Eduba numbers (Pacific Life >1,500 employees, 95% adoption, IAG/KPMG/Colgate) — SINGLE-publisher, no second source, do NOT treat as verified." — https://ai.miraheze.org/wiki/Interpretable_Context_Methodology — Learn AI / Miraheze — pub 2026-09-10 — low
- "Contradiction (structure): RinDig 5 layers vs Gist 3-layer vs Nathan 3-file trio + runner — three structural descriptions coexist." — gist vs RinDig — high

## NOT FOUND

- No primary supporting 'Intelligent Context Methodology'; no ReAct mapping; no token-savings eval with controlled comparison; no stars trajectory over time (snapshot only); no second confirmation of Eduba enterprise numbers; no retrospective threads beyond paper §4.6 + Nathan CHANGELOG. DuckDuckGo second query blocked (captcha) — lite fallback used.
