# The live site

A page that updates around the clock: a lead story chosen by significance, a wire ticker, and one river of
stories (newest first) that can be filtered by desk or shown as desk columns. Owner-approved layout: B with A
as a view (2026-10-30). Design boards: the "Live site" canvas artifact.

## Running it

    python live_service.py run       # the service; preview at http://127.0.0.1:8766
    python live_service.py sweep     # poll every source once, print what was queued
    python live_service.py render    # write feed.json from the store

Store: `.runtime/live_site/live.db`. Output: `.runtime/live_site/out/` (index.html, live.css, live.js, feed.json).
Events: `.runtime/live_site/events.jsonl`. Heartbeat: `.runtime/live_site/heartbeat.json` (only written while
every worker is beating; a stale file means restart the process). Settings: `config/live.yaml`.

## How a story moves

| Step | Who | What | Fails how |
|---|---|---|---|
| Wire | code | each source on its own clock: agency feeds 5 min, trade feeds 10, Federal Register 15, Google News one query per 30 s | 3 failures in a row: the source rests 5, 15, then 60 min |
| Read | code | resolve Google links, fetch the page, apply the edition's date / article / headline rules, drop off-topic and routine notices, merge the same report from another outlet into "Also reported by" | dropped with the reason |
| File | code | the edition's `infer_section`, then one of seven desks | none |
| Triage | writer (qwen, no thinking) | is this a portfolio story? | dropped |
| First pass | writer + code | one or two sentences; every name, number and date must be in the article; one strict rewrite | headline and link only |
| Check | verifier (gemma, other family) | every sentence entailed by the article (grounding.ground_summary) | two failures: headline only |
| Score | writer, thinking on | impact 0-5 and action class; code adds pickup, official source, U.S. relevance, novelty, penalties | two failures: stays first pass |
| Lead | code | `live_lead.decide`: bar = 50 + (lead + 8 - 50)·e^(-h/8); 45 min minimum; 90+ after 20 min; 30 h stale rule | none |

While the edition holds `.runtime/edition.lock`, model work pauses; stories are still read and filed and go up
as headline only after 20 minutes.

## Doctor levels

LIVE → LITE (checker unhealthy, its queue older than 30 min, or the edition has the GPU) → HEADLINES (writer and
fallback down) → FROZEN (the feed cannot be written; the page itself also shows "paused since" when feed.json is
more than 10 minutes old). Two writer failures in a row switch to qwen3.6; the primary is retried every 15 min.
A crashed worker thread is restarted; a story that crashes a worker twice is quarantined.

## Not done yet

- Publishing: the feed is local only until the owner picks delivery (Cloudflare edge, approval needed, or Pages).
- Scheduling: no Task Scheduler entry yet, and the services check does not yet restart the live service.
- Nightly copy chief (re-audit, source scorecard, corrections), the lead write-up with a critic pass, and
  Healthchecks "live-feed" (needs a new check URL stored with `scripts/set_secret.py`).
