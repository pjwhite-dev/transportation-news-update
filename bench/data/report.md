# Phase 2 summary benchmark

## Shape and speed (deterministic checks)

| Model | Days | Pass all shape checks | Avg words | Markdown links | Bare [url] links | Invented URLs | Banned phrases | Sec/day | Tok/s |
|---|---|---|---|---|---|---|---|---|---|
| qwen3.6 | 15 | 7% | 316 | 4.7 | 2.4 | 16 | 2 | 31 | 37 |
| qwen3.8 | 15 | 20% | 341 | 7.3 | 0.0 | 1 | 4 | 32 | 38 |
| gemma4 | 15 | 20% | 209 | 6.4 | 0.7 | 9 | 0 | 63 | 18 |

Published summaries average 122 words and have no links (the new spec is 220-350 words with inline links).

## Ranking (1 = best of four)

| Ranker | Days | qwen3.6 | qwen3.8 | gemma4 | published |
|---|---|---|---|---|---|
| owner | 15 | 2.67 | 2.80 | 2.33 | 2.20 |
| judge:qwen3.8 | 15 | 2.00 | 2.33 | 1.67 | 4.00 |
| judge:gemma4 | 15 | 1.93 | 2.40 | 1.93 | 3.73 |

### Win rate against the published summary

| Ranker | qwen3.6 | qwen3.8 | gemma4 |
|---|---|---|---|
| owner | 40% (15 days) | 40% (15 days) | 40% (15 days) |
| judge:qwen3.8 | 100% (15 days) | 100% (15 days) | 100% (15 days) |
| judge:gemma4 | 87% (15 days) | 87% (15 days) | 100% (15 days) |

## Claim support (sentence by sentence, by model judges)

| Judge | Writer | Sentences | Supported | Unsupported | Status errors |
|---|---|---|---|---|---|
| qwen3.8 | qwen3.6 | 190 | 60% | 72 | 4 |
| qwen3.8 | qwen3.8 | 196 | 62% | 74 | 0 |
| qwen3.8 | gemma4 | 151 | 85% | 21 | 2 |
| qwen3.8 | published | 69 | 55% | 28 | 3 |
| gemma4 | qwen3.6 | 190 | 62% | 73 | 0 |
| gemma4 | qwen3.8 | 196 | 62% | 75 | 0 |
| gemma4 | gemma4 | 151 | 87% | 18 | 1 |
| gemma4 | published | 69 | 68% | 19 | 3 |

## Critic (qwen3.8, thinking on)

- Rejected 7 of 7 known-bad summaries (must be all).
- Passed 2 of 9 good candidate summaries (false rejections are expected to be rare).

  - shipped:2026-10-21: REJECT (avg 1.25, 4 blocking defects)
  - shipped:2026-10-22: REJECT (avg 1.25, 4 blocking defects)
  - synthetic:template_text: REJECT (avg 1.75, 2 blocking defects)
  - synthetic:headline_list: REJECT (avg 1.62, 2 blocking defects)
  - synthetic:workflow_language: REJECT (avg 3.88, 1 blocking defects)
  - synthetic:status_mismatch: REJECT (avg 3.62, 1 blocking defects)
  - synthetic:invented_fact: REJECT (avg 3.38, 1 blocking defects)
  - good:qwen3.6:2026-10-25: REJECT (avg 3.88, 0 blocking defects)
  - good:qwen3.8:2026-10-25: REJECT (avg 4.0, 2 blocking defects)
  - good:gemma4:2026-10-25: PASS (avg 4.0, 0 blocking defects)
  - good:qwen3.6:2026-10-26: REJECT (avg 3.38, 3 blocking defects)
  - good:qwen3.8:2026-10-26: REJECT (avg 3.88, 3 blocking defects)
  - good:gemma4:2026-10-26: PASS (avg 4.5, 0 blocking defects)
  - good:qwen3.6:2026-10-05: REJECT (avg 4.12, 1 blocking defects)
  - good:qwen3.8:2026-10-05: REJECT (avg 4.12, 1 blocking defects)
  - good:gemma4:2026-10-05: REJECT (avg 3.88, 0 blocking defects)

## Critic (gemma4, thinking on)

- Rejected 7 of 7 known-bad summaries (must be all).
- Passed 8 of 9 good candidate summaries (false rejections are expected to be rare).

  - shipped:2026-10-21: REJECT (avg 1.88, 2 blocking defects)
  - shipped:2026-10-22: REJECT (avg 2.25, 2 blocking defects)
  - synthetic:template_text: REJECT (avg 2.0, 2 blocking defects)
  - synthetic:headline_list: REJECT (avg 2.0, 1 blocking defects)
  - synthetic:workflow_language: REJECT (avg 4.88, 1 blocking defects)
  - synthetic:status_mismatch: REJECT (avg 4.38, 1 blocking defects)
  - synthetic:invented_fact: REJECT (avg 4.62, 1 blocking defects)
  - good:qwen3.6:2026-10-25: PASS (avg 5.0, 0 blocking defects)
  - good:qwen3.8:2026-10-25: PASS (avg 5.0, 0 blocking defects)
  - good:gemma4:2026-10-25: PASS (avg 5.0, 0 blocking defects)
  - good:qwen3.6:2026-10-26: PASS (avg 5.0, 0 blocking defects)
  - good:qwen3.8:2026-10-26: PASS (avg 5.0, 0 blocking defects)
  - good:gemma4:2026-10-26: PASS (avg 5.0, 0 blocking defects)
  - good:qwen3.6:2026-10-05: PASS (avg 5.0, 0 blocking defects)
  - good:qwen3.8:2026-10-05: REJECT (avg 4.62, 2 blocking defects)
  - good:gemma4:2026-10-05: PASS (avg 5.0, 0 blocking defects)
