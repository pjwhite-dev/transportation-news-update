# Oct 29 edition: round-2 fixes (A-F) replayed

Same raw feed (`data/raw_archive/2026-10-29.json`), dry run, with production's resolved-URL cache from the 04:30 run.

| Run | Final stories | Gates | Notes |
|---|---|---|---|
| Production 04:30 | 20 | passed | Spain crash as 3 separate items |
| Round 2, replay 2 | 25 | passed | verifier checks failed silently (200-token budget): Spain still 3 items |
| Round 2, replay 3 | 25 | passed | Spain merged, then dropped as a repeat of Oct 28 (30 -> 68 dead not counted as news) |
| Round 2, final | 25 | passed | Spain one item, marked an update ("new casualty count: 68"); White House proclamation linked as Official source |

Final replay QA: 4 same-event merges (verifier), 14 scope events (incl. incidental-topic drops), 4 edition-cap cuts, 1 official source.
After the final replay: Additional coverage lists each outlet once, at most 6 links (the Spain item had 14, one outlet twice); unit-tested, not replayed.
Not shown live: the agency-site search for the FRA $2.3B story (tests pass; the local SearXNG upstreams were rate-limited by repeated testing that afternoon). Most agency sites (FAA, NHTSA, FCC, DOT, FMCSA, Commerce) return 403 to our fetcher, so such results are judged on the search snippet.
