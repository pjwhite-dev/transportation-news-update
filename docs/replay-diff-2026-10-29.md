# Replay of the 2026-10-29 edition with audit fixes 1-5

Same input for every run: `data/raw_archive/2026-10-29.json`, `--dry-run --as-of 2026-10-29`, local Ollama (qwen3.8 writer, gemma4 verifier).

| Run | Code | Candidates | Entering quality pass | Final | Gate |
|---|---|---|---|---|---|
| Published 04:30 | main | - | 29 | 20 | passed |
| Control replay | main | 142 | 28 | 18 | passed |
| Branch replay 1 | fix/audit-1029 | 138 | 17 | 14 | refused (minimum 15) |
| Branch replay 2 | fix/audit-1029 | 133 | 40 | 34 | passed |

Branch replay 1 lost its stories in the writer's story selection, before any code these fixes touch; replay 2 of the same code kept 40. The shortfall was model variance, and the minimum-story gate caught it as designed.

## What changed in the edition (published vs branch replay 2)

Gone, for the intended reason:
- "Counter-Drone Systems Explained: How They Work and Cost" (strikeorbit.com): explainer, not news (fix 3). Two more explainers were also dropped.
- "Urban rail industry news round-up": round-up, not news (fix 3).
- "The FCC Is About to Put Drones on Cell Networks..." (seekingalpha.com/instablog): contributor blog, denied host (fix 3).
- "The US Military's $1 Billion Drone Plan..." (tuzarapost.substack.com): denied host (fix 3).

New or moved:
- Federal Policy & Implementation, empty in the published edition, now has 3 stories: the Trump robotics-import proclamation (moved from Robotics, fix 4), FRA's $2.3B CRISI awards, and the USAST designation order.
- The Executive Summary now leads with the proclamation and the FRA awards (tier 1), then the Spain crash.
- "Waymo expands driverless ride service into South Bay" is kept (fix 2: no longer caught by the celebrity-ride filter).
- The summary has no sentence opening with a bare "Embassy in Riyadh ..." (fix 5).

Not attributable to the fixes: the rest of the story mix (for example Zambia-Angola rail and Transmashholding in the published edition, Sentosa AV and Foundry Robotics in the replay) varies run to run on unchanged code, as the control shows.
