# Pipeline map (written 2026-10-27, before the quality overhaul)

Legend: **M** = uses a local model, **D** = deterministic code. "New" marks the checks added in the overhaul; each
sits inside an existing stage. No stage was rewritten.

| # | Stage | Where | M/D | What it does | New checks added here |
|---|---|---|---|---|---|
| 1 | Source discovery | `news_engine.collect_articles`, `web_discovery.py`, GitHub `daily-news-update.yml` (04:15) | D | Google News RSS, SearXNG, official RSS, Federal Register API into `data/latest_raw_news.json` (`raw-feed` branch) | none (discovery stays broad; filtering is later) |
| 2 | URL resolution | `url_resolver.resolve_articles` | D | Google News wrapper links to publisher URLs; unresolved are dropped | none |
| 3 | Fetch / parse | NEW `article_extract.enrich_candidates` (called from `automated_briefing.build_briefing`) | D | Fetches each candidate's own page once; saves the HTML to `.runtime/fetched/<date>/` | real publication date, article-ness, body text, clean headline |
| 4 | Date handling | `article_extract.parse_date`, `calendar_rules.freshness_hours` | D | Date order: JSON-LD, meta, labelled "Published" text, byline beside the headline, `<time>`, URL; official dates for Federal Register records | 48 h window (72 h over weekends and federal holidays), configurable in `config/editorial.yaml`; no date means dropped |
| 5 | Classification | `news_engine.analyze_articles` (batches of 35), `infer_section`, `validate_analysis` | **M** + D | Model clusters records, picks section and importance; regex rules re-section | section scope test after classification (stage 9) |
| 6 | Summarization | `news_engine.cluster_to_story` then NEW `grounding.ground_summary` | **M** | Story summaries were the model's batch output from RSS snippets | summary rewritten from the primary article text only, then deterministic grounding, then a second model (gemma) entailment check, one strict regeneration, extractive fallback |
| 7 | Dedup | `news_engine.deduplicate_articles`, `merge_duplicate_clusters`, `coverage_history.annotate_previous_coverage` (model sees a hint), NEW `story_groups.py` | D | Title similarity only | same-event groups by Federal Register number, RIN, docket, House report, URL; cross-edition store of the last 7 days (`data/published_groups.json`) drops repeats without new facts |
| 8 | Executive summary | `summary_chain.run_chain` | **M** (writer qwen, verifier gemma) + D | Fact sheet of top stories, sentence-level verification, numbered citations | fact sheet ordered federal policy, then company, then foreign/military; stories that led either of the last two editions may not lead; one item per event |
| 9 | Section scope | NEW `editorial_checks.enforce_section_scope`, called from `quality_pass` and `normalize_reader_features` | D | Keyword scope tests per section in config | reassign or drop; no catch-all; celebrity/war/crime filler dropped |
| 10 | Docket tracker | `tracker/core.py`, `regulatory_tracker.py` | D | Federal Register API events to status | status from FR document type and action; OIRA review from reginfo.gov; FCC docket; daily discovery of new rulemakings |
| 11 | Market data | `market_watch.py` (Finnhub) | D | Quotes to `data/market.json` | session label from NYSE calendar and quote timestamps |
| 12 | HTML render | `public_site.py`, `site_assets/` | D | Static site | real publication date shown, clean source names, five-column docket, italic executive-order titles with a footnote |
| 13 | Gates and repair | `validation_gates.py`, `automated_briefing.repair_briefing` | D | Hard gates, item-level repair | docket gate also requires the FCC docket |
| 14 | QA report | NEW `qa_report.py` | D | none before | `qa/YYYY-MM-DD.json` and `.md`: every drop, move, regeneration and the reason |
| 15 | Publish | `scripts/run_local_pipeline.ps1`, GitHub Pages workflow | D | Commit, push, verify live | commits `qa/`, `tracker/oira.json`, `tracker/discovered.yaml`, `data/published_groups.json` with the edition |

Order inside one run: 2 → 3/4 (drop stale, non-article, bad headline) → 5 (model) → 6 pre-step (stories built) →
`quality_pass` = labels, scope, groups, repeats, grounded summaries, style, scope again, lead eligibility → 8 (model) →
13 gates and repair → 14 report → 15 publish.
