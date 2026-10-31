# The live site

A page that updates around the clock: a wire ticker, a lead story chosen by significance, a market strip, and one
full-width feed of stories (newest first), each labelled with its category on the left; readers switch categories
on and off. Owner review 2026-10-30 replaced the desk columns with this single feed. Design boards: the "Live
site" canvas artifact (the mockup's "lead bar" panel was never on the real page).

## Running it

Task `newsroom-live` (at logon, from `C:\newsroom`, registered by `scripts/windows/install_tasks.ps1`) runs `live_service.py run`
with no time limit. `scripts/ensure_services.ps1` (every 5 min) restarts it when `.runtime/live_site/heartbeat.json`
is missing or older than 10 minutes, unless the task started in the last 10 minutes.

    python live_service.py run       # the service; preview at http://127.0.0.1:8766
    python live_service.py sweep     # poll every source once, print what was queued
    python live_service.py render    # write feed.json from the store

The preview serves only while `run` is running. Store: `.runtime/live_site/live.db`. Output: `.runtime/live_site/out/` (index.html, live.css, live.js, feed.json).
Events: `.runtime/live_site/events.jsonl`. Heartbeat: `.runtime/live_site/heartbeat.json` (only written while
every worker is beating; a stale file means restart the process). Settings: `config/live.yaml`.

## How a story moves

| Step | Who | What | Fails how |
|---|---|---|---|
| Wire | code | each source on its own clock: NASA and DoD feeds 5 min; FAA, DOT and White House by Google News `site:` search every 10 (their feeds refuse us); trade feeds 10; Federal Register 15; Google News one query per 30 s | 3 failures in a row: the source rests 5, 15, then 60 min |
| Read | code | resolve Google links, fetch the page, apply the edition's date / article / headline rules, drop off-topic and routine notices, merge the same report from another outlet into "Also reported by" | dropped with the reason |
| File | code | the edition's `infer_section`, then one of seven categories (`desks` in config) | none |
| Triage | writer (qwen, no thinking) | is this a portfolio story? | dropped |
| First pass | writer + code | one or two sentences; every name, number and date must be in the article; one strict rewrite. Page label "Quick summary" | headline and link only |
| Check | verifier (gemma, other family) | every sentence entailed by the article (grounding.ground_summary). Page label "Summary double-checked" (never "verified": we check our summary, not the reporting) | two failures: headline only |
| Score | writer, thinking on | impact 0-5 and action class; code adds pickup, official source, U.S. relevance, novelty, penalties | two failures: stays first pass |
| Lead | code | `live_lead.decide`: bar = 50 + (lead + 8 - 50)·e^(-h/8); 45 min minimum; 90+ after 20 min; 30 h stale rule | none |

Markets: `live_market.py` refreshes the Finnhub watchlist every 5 minutes while NYSE is open and once after each
close (`.runtime/live_site/market.json`, falling back to the edition's `data/market.json`).

While the edition holds `.runtime/edition.lock`, model work pauses; stories are still read and filed and go up
as headline only after 20 minutes. The owner can pause model work the same way until a set time with
`.runtime/live_site/gpu_pause.json` (`{"until": "<ISO time>"}`); it lapses by itself, and unloading the models
(`ollama stop <model>`) frees the card at once.

**Sorting** (`live_sort.py`, worker "sort"): once a story is on the page, one short writer call per story names the
earlier report (of up to eight sharing names or figures, within two days) that covers the same event, and says
whether the story is about the United States. Same-event stories share `group_id`; the feed shows each event once
(the most-checked report, then an official source, then the first) with the other outlets under it, and only
summarized U.S. stories, one per event, ride the wire bar. `record._scope` "world" stories are hidden in the page's
default "U.S. focus" view and shown under "Everything". Sorting waits while the GPU is held; three failures leave
a story on its own, counted as U.S. The dashboard shows how many stories wait to be sorted.

There is no policy desk: a rule, law or agency action goes on the desk of what it is about (the triage model picks
it; before that, stories from the edition's policy sections, `topic_sections` in config, are filed by their words).

FAA and DOT sites refuse the reader (HTTP 403 to its own User-Agent). Their releases, found by the wire's news
searches, go up as headline and link only, dated by that search and never summarized (config
`desk.headline_only_pages`; the writer still judges relevance from the headline). The reader obeys each site's robots.txt (`article_extract.robots_allows`). The request
asking the agencies to let the reader in is in `docs/agency-access-request.md`.

## Doctor levels

LIVE → LITE (checker unhealthy, its queue older than 30 min, or the edition has the GPU) → HEADLINES (writer and
fallback down) → FROZEN (the feed cannot be written; the page itself also shows "paused since" when feed.json is
more than 10 minutes old locally, 25 minutes on the public site). Two writer failures in a row switch to qwen3.6; the primary is retried every 15 min.
A crashed worker thread is restarted; a story that crashes a worker twice is quarantined.

## Public site and the owner's override

- **Page**: https://advancedtransportation.live/ (GitHub Pages; `public_site.py` copies `site_assets/live/` to the root; the morning edition is at /edition/ and /live/ redirects to the root).
- **Feed**: `live_push.py` commits `feed.json` to the `live-feed` branch (worktree `C:/newsroom/.wt/live-feed`)
  and pushes, never forced: when the stories change (at most every 3 min) and every 10 min regardless; from
  midnight to 4 a.m. Eastern (`push.overnight`) only once an hour, on the hour. The page reads it from
  raw.githubusercontent.com (GitHub caches it up to 5 min), polls every 60 s, and says "paused" after 25 min
  without a new copy (75 min for an overnight copy, when it also tells readers it updates hourly). `.runtime/live_site/push.json` records the last push for the dashboard.
- **Page features**: every story has an address (`#s-<id>`, "Copy link"; a link to any outlet's report finds
  its event's entry); the desks and view are in the address (`?desk=av,rail&view=all`); the lead is a compact card
  unless it is in its first hour, rated Significant, or the owner's pick; "Pause" in the wire bar stops all motion,
  as the device's reduced-motion setting does; link previews use `og-card.png` (`scripts/make_og_card.py`).
- **Headline override**: https://advancedtransportation.live/login/ (not linked from the page, `noindex`). It opens
  on a sign-in form: the owner pastes a fine-grained GitHub token (this repository only, Issues read and write); the
  page asks GitHub's API whose token it is and shows the story list only for `pjwhite-dev` with access to the
  repository. The token stays in that browser (session, or kept on the device if ticked) and goes only to
  api.github.com. Pick a story and 1-48 hours; the page files the GitHub issue itself ("Headline override: ...";
  body `story:` and `hours:`; `hours: 0` ends it). `live_override.py` reads open issues every minute with the stored `github_token` and applies
  only those opened by `pjwhite-dev` and less than 6 hours old, then answers and closes them. While an override
  holds, no story replaces the lead; when it ends, the best checked story of the last day scoring 50+ leads, or
  none does.

## Not done yet

- Nightly copy chief (re-audit, source scorecard, corrections), the lead write-up with a critic pass, and
  Healthchecks "live-feed" (needs a new check URL stored with `scripts/set_secret.py`).
