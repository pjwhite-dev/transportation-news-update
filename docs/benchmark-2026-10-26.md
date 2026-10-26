# Phase 2 benchmark: Executive Summary writers (2026-10-26)

Full tables: `bench/data/report.md`. Harness: `bench/` (`python -m bench.run_all`, owner judging page `python -m bench.serve`).
15 archived days (incl. the two template editions Oct 21 and 22), identical 25-story fact sheets, three local writers
(qwen3.6:27b-q4_K_M, qwen3.8:27b-q4_K_M, gemma4:31b), baseline = the summary actually published that day. Owner ranked blind;
qwen3.8 and gemma4 (thinking on) ranked blind and fact-checked sentence by sentence.

## Result: acceptance NOT met, by any model
| Criterion (plan 7.10) | qwen3.6 | qwen3.8 | gemma4 |
|---|---|---|---|
| Owner win rate vs published, need >= 70% | 40% | 40% | 40% |
| Sentences supported by the fact sheet, need >= 98% | 60-62% | 62% | 85-87% |
| Status errors, need 0 | 4 / 0 (two judges) | 0 / 0 | 2 / 1 |
| Invented URLs over 15 days | 16 | 1 | 9 |
| Spec shape (220-350 words, links, no banned words) | 7% | 20% | 20% |
| Critic rejects every known-bad (7 of 7) | qwen3.8 critic: yes, but rejects 7/9 good; gemma4 critic: yes, passes 8/9 good (but scores padded summaries 5.0) |

## What the owner ranked
Published first on 9 of 15 days: all 8 early days (short, tight, 60-170 words) and none of the template-period days (models beat
published on 6 of 7 of those). Model judges disagree with the owner: they rank published last on every day (length bias), so they cannot
stand in for the owner.

## Findings
- "Unsupported" sentences are mostly interpretive gloss ("signals sustained federal investment", "aligns with a broader push"), not
  invented facts. This is exactly the editorializing the style rules ban; it is a prompt and spec problem, and longer targets make it worse.
- Gemma writes shorter, tighter and more grounded (85-87% supported) but is 2x slower (63 s vs 31 s) and invents some URLs.
- qwen3.8 is the most faithful on wording (0 status errors) and links (1 invented URL) but pads the most (341 words average).
- The fact sheet is only headline + short summary per story, which caps any model; full-text extraction (Phase 1) is needed for 98%.
- Critic: a rubric critic alone is not enough (gemma4 critic gives 5.0 to summaries the fact-checker found 40% unsupported); the
  sentence-level support check is the better gate. qwen3.8 critic is too strict (needs calibration).

## Next steps
1. Writer cites story ids ([S3]); code substitutes real URLs, so no model can invent a link.
2. Evidence-only prompt: every sentence must rest on a named story, no interpretation sentences, shorter target; rerun gemma4 and qwen3.8.
3. Add the sentence-level support check as a publish gate and calibrate the critic.
4. Article text extraction to enrich fact sheets (Phase 1 item 3).
