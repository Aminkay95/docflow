# Token-test hunter digest (Deepen)

All accessed 2026-11-09. 5 primaries + 5 DDG pages.

## ICM 2K-8K vs 30K-50K — UNVERIFIED
- Figure 3 + 3.2: staged ~4.9k/5.5k/5.6k, monolithic ~42k; range 2,000-8,000 vs 30,000-50,000; caption 'representative token counts from script-to-animation workspace'. — https://arxiv.org/html/2603.16021v2 — arXiv/Eduba — pub 2026-03-18 — high — token-test
- 4.6 Threats: 'No controlled comparison has been conducted' — benefit rests on lost-in-the-middle theory + practitioner judgment, not measured effect. No benchmark/experiment beyond that. — same 4.6 — high
- Absence search: DDG 'ICM tokens benchmark staged vs monolithic' → only recyclers (RinDig, ktnCodes/icm-template, ap0110 wiki, LvlyBot gist) + unrelated ICMs (ICM-Bench In-Context Modification, Infinite Context Memory). No second publisher with new token data.

## ReAct 34/10 — DISPUTED on magnitude
- Original: 'outperforms imitation/RL by absolute success rate 34% and 10%' — https://arxiv.org/abs/2210.03629 — Yao et al. — v1 2022-10-06, v3 2023-03-10 — high
- Independent reproduction, 6,268 trajectories, qwen3.6-35b: ALFWorld ReAct 12.69% vs Act 7.46% (+5.22pp, CI [1.49,9.70]); WebShop ReAct 35.6%/57.72 vs Act 43.6%/71.27 (ReAct worse by 8.0pp). — https://github.com/GeYugong/react-reproduction — GeYugong — pub 2026 — medium (sole reproducer, clear methods, low stars). Different model/environment so not strict rerun; dispute is pattern fragility, not exact rerun.
- BrowseComp primary identified, not opened (budget): https://openai.com/index/browsecomp/ + arXiv 2504.12516 (1,266 questions).

## Multi-agent 90.2%/80%/15x — UNVERIFIED exact, directionally conditionalized
- Source: multi-agent outperformed single-agent by 90.2% on internal research eval; agents ~4x tokens, multi-agent ~15x; three factors 95% variance, token alone 80%. — https://www.anthropic.com/engineering/built-multi-agent-research-system — Anthropic — pub 2025-06-13 — high for claim, low for generalizability (internal, unpublished)
- Recyclers (=ONE publisher): ajayk.sh, getnadir.com, infomly.com, devectra.com, medium RSATech, TheAIEngineer Substack, backfield.net — all cite Anthropic, no own data.
- Conditional second source: 180-config eval (blog) / 260 configs x 6 benchmarks x 5 architectures x 3 families (paper): centralized +80.9% on parallelizable Finance-Agent; every multi-agent variant degraded sequential PlanCraft 39-70%; R2=0.513; errors amplify 17.2x vs centralized 4.4x. — https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/ + https://arxiv.org/abs/2512.08296 — Google Research Kim & Liu — pub 2026-01-28 blog / 2025-12 arXiv — high

## Verdicts
- (a) ICM tokens: UNVERIFIED.
- (b) ReAct 34/10: DISPUTED on magnitude (+5.2pp not +34; WebShop negative).
- (c) 90.2%/80%/15x: UNVERIFIED exact; directionally corroborated & conditionalized by Google.

## Not found
- No ICM controlled experiment anywhere including paper 4-6.
- No second Anthropic replication (undisclosed eval set).
- Leads: openai.com/index/browsecomp/, arxiv 2504.12516, astrocvijo react_reproduction.pdf, Google paper PDF token table.
