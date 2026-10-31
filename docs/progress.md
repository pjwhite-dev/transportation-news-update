# Progress log

Append a dated entry at the end of every work session: what changed, tests run, what is next.

## 2026-10-24 — Session 1 (Section 1 + start of Phase 0)
Changed:
- Section 1 done: docs/rebuild-plan.md (verbatim brief), docs/editorial-rules-legacy.md, rewritten AGENTS.md, new CLAUDE.md, docs/progress.md, docs/decisions.md, .claude/settings.json.
- Phase 0 item 1 done: root cause of the Oct 23 miss found, docs/incidents/2026-10-23.md. Ollama was down at 04:16; the Hermes build had no recovery and nothing alerted.
- Phase 0 item 2 done: Hermes scripts snapshotted to legacy/hermes_path/; Hermes edition/publish/repair/self-heal cron jobs paused; n8n workflow deactivated and stopped.
Tests run: none (no code changed yet). Verification was read-only: git, event logs, Hermes cron list, n8n DB.
Not done / notes: `gh` CLI is not installed (needed for Actions inspection, PR/merge automation). Phase 0 items 3-9 not started. As of now NOTHING publishes editions (Hermes and n8n paused) until the interim publisher (item 3) is ready; the Oct 24 raw collector still runs.
Next: interim publisher changes (streaming Ollama client + 120s stall timeout, free-text prose, remove llama3.3), fail-hard on fallback text, hard gates in validation + CI, collector off main, tracker restore.

## 2026-10-24 (later) — Phase 0 interim publisher, branch phase0/interim-publisher
Changed (not yet merged to main; see decisions.md):
- ai_providers: streaming + 120 s no-data stall timeout, free-text generate_text, OpenAI/Fallback equivalents.
- news_engine: Executive Summary free text; template fallback removed (raises instead).
- validation_gates.py: freshness, template text, headline echo, duplicates, relevance, links, tracker, provenance, schema; validation_report in the edition JSON. Known-bad Oct 21/22 editions kept in tests/regression/known_bad and must fail.
- tracker/: 10 required RINs, append-only Federal Register ledger seeded live (all 10 fetched, official links 200), fixed status rules, verification log. `python -m tracker.core` refreshes it; the pipeline refreshes it each run.
- url_resolver.py: Google News wrappers -> publisher URLs (8/8 live samples resolved); unresolved candidates are dropped.
- alerts.py + scripts/set_secret.py: Telegram + Healthchecks via keyring; pipeline alerts on any failure and pings edition-verified on success.
- scripts/ensure_edition.ps1, install_tasks.ps1, install_services.ps1 (admin, not yet run); run_local_pipeline.ps1 takes a run lock and uses --require-today.
- CI: gate/regression tests + hard-gate re-validation before the site build; raw collector commits to `raw-feed`, not main.
Tests: new suites 100% green (gates 17, tracker 11, providers, resolver 6, alerts 7). Full suite: 47 failures, all pre-existing (docs/test-baseline.md).
Found: SearXNG was down because WSL was stopped (started it; boot task is in the admin batch). The Ollama model qwen3.6:27b-q4_K_M was not installed (pull started); qwen3.8:27b-q4_K_M is installed.
Next: confirm a real dry-run edition end to end, merge to main, run the first live edition, then Phase 1 (runner, ledger, C:\newsroom). Waiting on docs/phase0-requests.md (credentials + admin steps).

## 2026-10-25 — First edition through the fixed path (Phase 0 items 3-7, 9)
Changed: batched story analysis (35 per call; the single 300-candidate call produced 4-7 story days), interim daily cut (top 45, 12 per section, importance >= 3, editor-vetted never cut), pre-gate prune (dead links, non-article pages, headline-echo summaries; every removal recorded in pruned_items), minimum-story-count gate, retry now ends on a user turn (Ollama rejected system-last).
Run: C:\newsroom clone + venv; scripts/run_local_pipeline.ps1 published the 2026-10-25 edition (30 stories, 10/10 tracker, all gates passed, 4 items pruned, 560 s model time) and verified it live on news.peterjwhite.org. First edition from the new code; replaces the template-text Oct 22 edition on the site.
Tests: new suites green; full suite = the 47 pre-existing failures (docs/test-baseline.md).
Not done: Telegram/Healthchecks unconfigured (alerts print "not configured"), boot services/tasks not installed (need admin + credentials, docs/phase0-requests.md), Anthropic key not set so the local model still writes the Executive Summary, GitHub CLI absent so CI result not inspected, OneDrive copy untouched (Phase 1 uses C:\newsroom).
Next: owner runs docs/phase0-requests.md; then verify Monday 2026-10-26 04:30 run; Phase 1 runner/ledger.

## 2026-10-26 (01:15 ET) — Unattended infrastructure installed
Done: Telegram + Healthchecks + GitHub token stored in Credential Manager (verified: alert delivered, pc-alive green); services newsroom-ollama and newsroom-hermes-gateway running as boot services under the owner's account; newsroom-wsl boot task (SearXNG answering); tasks newsroom-edition (weekdays 04:30), newsroom-ensure (15 min), newsroom-heartbeat (5 min, ran OK from the scheduler, proving keyring works there), newsroom-tracker (04:00 daily). Fully-local override recorded (docs/plan-overrides.md).
Not verified: first unattended edition (Monday 2026-10-26 04:30) — git push and keyring from a signed-out task are untested; reboot recovery untested; WSL linger not set; BIOS power-on setting pending.
Next: check the 04:30 run (git log, Telegram, edition-verified ping); then Phase 1 runner/ledger.

## 2026-10-26 (02:00 ET) — Night Desk redesign + non-article filter
Done: tightened non-article filter (merged earlier, 55788ab); redesigned the public site (hero, mobile menu, collapsed docket, sidebar watch list, archive by month with lead headlines, client-side archive search over headlines only), renamed to Advanced Transportation Update. 10 new design tests pass; full suite unchanged vs baseline.
Verified locally (built from real data, desktop + 375px phone): hero, stories, docket collapsed (summary "10 rulemakings tracked · Next date: November 25, 2026"), menu opens, no sideways scroll, search returns 86 hits for "waymo".
Not done: market-data panel (awaiting owner decision); live site not yet viewed after deploy; Phase 2 will replace the interim hero/lead selection and add per-story summaries/status classes.

## 2026-10-26 (09:30 ET) — Morning failures fixed; infrastructure moved to the signed-in session
Failures this morning: (1) the 04:00 tracker task dirtied tracker/verifications.jsonl, so the pipeline refused to run at 04:30 and on four retries (fixed: ledger files are discarded before the clean-tree check); (2) the password-logon tasks stopped firing about 06:20 and Windows rejected the owner's Microsoft-account password for task/service logons (0x8007052e, runas 1326), so nothing could be re-registered; (3) the "verified live" check passed early on free text, now requires the exact <meta name="edition-id">.
Done: edition for 2026-10-26 published manually at 08:53 (31 stories, gates passed, live). install_tasks.ps1 rewritten: no password, interactive tasks; Ollama, Hermes gateway and the WSL keep-alive are hidden sign-in tasks restarted by newsroom-services-check (every 5 min); NSSM services removed. Owner ran it; heartbeat and services-check confirmed firing on schedule (09:20, result 0); Ollama and SearXNG answering. Run transcripts now go to .runtime/logs. Task Scheduler history enabled.
Not verified: the unattended 04:30 run on Tuesday 2026-10-27 (first real test of the new task setup, transcripts, Telegram success note and the new edition-id check); behavior after a reboot (needs sign-in); `newsroom-ensure` firing at 09:30.
Pending: Market watch (owner said yes; on hold until the failures are cleared); Phase 2 benchmark (owner: later); the old "Advanced Transportation n8n" scheduled task is still present (harmless, remove in Phase 4); flowchart saved only in the session (not in the repo).

## 2026-10-26 (evening) — Phase 2 benchmark and the verified summary chain
Benchmark (docs/benchmark-2026-10-26.md): no raw model met acceptance; ChatGPT and Claude blind-judged 6 days x 5 candidates; the new chain was most accurate (0.00 unsupported/day). Chain merged to main for the 2026-10-27 04:30 run. Full pipeline dry run with qwen3.8 + gemma4 passed every gate (28 min, 22 stories, summary verified by gemma4:31b, 1 sentence dropped). Grammar/fragment check added after ChatGPT and the dry run found "Army to intercept unmanned aerial systems."
Not verified: the unattended 04:30 run with the chain; the headless-task installer re-run (owner, admin) for the console-window flash; Market watch (needs tickers + free key decision). The flowchart in the session predates the chain (no verifier step).

## 2026-10-27 (07:20 ET) — Tuesday run failed; fixes and self-healing
Failure: first unattended run with the chain generated and verified a summary in ~35 min, then failed the links gate on one dead "also covered" URL; the four 15-minute retries refused to start (dirty tree from the failed run) and alerted each time. Fixed on main (a667a7b): dead also-covered links are pruned, retries survive a failed run's leftovers. Edition run started manually at ~07:20.
Self-healing built on branch feat/self-heal (tests pass, same 43 pre-existing failures as the baseline): repair loop for item-level gate failures, draft checkpoint so retries skip generation, quiet retry alerts. Not yet merged (waiting for the manual run to finish so C:\newsroom is not changed mid-run). Market watch, new masthead and left "In this edition" column are live on main.
Not verified: the repair loop and the draft resume on a real run; Tuesday's edition publishing.

## 2026-10-27 (evening): doctor tuning and raw-feed workflow
- Local doctor (doctor.py, scripts/approve_doctor.ps1, .runtime/incidents.jsonl): weekday 08:15 check; classifies the failing log, asks the local model for a diagnosis, and only prepares a doctor/<date> branch when the fix has exact search-and-replace edits plus a regression test that fails before and passes after, with no new failing tests. Repeats escalate to a critical Telegram alert. The owner approves with approve_doctor.ps1.
- Tuning: the doctor's first dry run on the 10-27 dead-link failure called it "operational". Fixes: gate failures now show validation_gates.py to the model, the prompt treats a one-item gate failure as repairable (gates themselves never touched), a gate-specific task line is appended, and an empty code_fix is retried up to 4 times. Measured on the pre-fix code: about 1 in 4 single tries gave a usable fix before retries.
- Daily Transportation Raw News Collection failed on 10-26 and 10-27 at the "commit to raw-feed" step (collection itself succeeded). Likely cause (job logs need a login so unconfirmed): `git checkout -B raw-feed` aborted because main's data/latest_raw_news.json, published by the local run, differs from raw-feed's. Changed to `git checkout -f -B`; the data was already copied to /tmp. Verify on the next 04:15 run.
- Doctor self-approval added (doctor.auto_merge_ok, apply_branch, incident_count; tests/test_doctor_auto.py). See decisions.md.

## 2026-10-27 (evening): editorial quality overhaul
Branch `feat/editorial-quality`. New modules: `article_extract.py` (dates, headlines, article-ness), `calendar_rules.py`,
`editorial_config.py` + `config/editorial.yaml`, `grounding.py`, `story_groups.py`, `editorial_checks.py`,
`quality_pass.py`, `qa_report.py`. Changed: `tracker/core.py` (status from FR data, OIRA, FCC docket, discovery),
`market_watch.py`, `public_site.py`, `summary_chain.py`, `automated_briefing.py`, `news_engine.py`, pipeline script.
Pipeline map: `docs/pipeline-map.md`. 48 new regression tests in `tests/test_editorial_quality.py` (fixtures in
`tests/fixtures/editorial/`; `*_reconstructed.html` files could not be fetched because congress.gov and AP return 403).
Live yield check on the Oct 27 raw feed: 285 resolved candidates, 120 passed the article check.

## 2026-10-27 (night): replays of the 2026-10-25 to 10-27 editions
Dry-run replays (`--dry-run --raw-file data/raw_archive/<day>.json --as-of <day>`) of the three editions through the new pipeline; before/after in `docs/replay-diff.md`, QA reports in `docs/replay-qa/`. Each replay takes about 40 minutes, the same as the current production edition.
- First replays: Oct 27 passed (32 stories), Oct 26 passed (23), Oct 25 was refused at 5 stories (minimum 12).
- Replay-driven fixes: repeat matching used the loose headline test and dropped two different stories (Archer, Siemens/CHSR); now strict. A "Published:" line that appears only in the page title now dates the article. A NASA flight-test photo story led the Oct 27 summary; a government source is now a policy action only when the text is about a rule, proposal, grant or order. "cuas" and "A250" were missing from the scope terms. A refused edition now still writes its QA report (`.runtime/failed-qa/`). Non-Latin and mis-cased publication names are mapped to English names (`name_fixes`, `source_names` in `config/editorial.yaml`).
- Final replays: Oct 25 29 stories, Oct 27 30, Oct 26 see `docs/replay-diff.md`. The cyan-lights story is a full item on Oct 25 only.
- Full test suite: the same 43 failures as main (identical test ids), no new ones.
Not verified: the unattended 04:30 run with the new checks; the next 04:15 GitHub raw-collection run (the raw-feed fix).

## 2026-10-28 (07:45 ET): the 04:30 run and all four retries failed in two seconds
Cause: my own change to `scripts/run_local_pipeline.ps1` (quality overhaul) added `tracker/discovered.yaml` to a single `git checkout -- a b c ...` that discards regenerated files. That file is not tracked yet, git aborted the whole command ("pathspec did not match"), PowerShell turned it into a terminating error, and the run stopped before generating anything. The scripts were never run end to end before merging; only the Python was tested.
Fix (branch fix/preflight): the cleanup moved into `edition_preflight.py`, which checks each path with `git ls-files` and is unit-tested against a real temporary repository (`tests/test_edition_preflight.py`, including a lint that rejects multi-path `git checkout` in the script). The doctor can now edit `scripts/*.ps1|py`, `config/*.yaml` and `tracker/rulemakings.yaml`, classifies PowerShell `TerminatingError` failures and reads the failing script, merges repeat fixes too (size limit 150 added / 30 removed lines), reruns the edition once a day for Ollama and dirty-tree failures, and is started by the ensure task after the second failed attempt instead of waiting for 08:15. See decisions.md.

## 2026-10-28 (evening): three more blockers closed
- CI emails: GitHub's re-check could not reach finance.biggo.com; BigGo and Pluang denied; the doctor now denies such hosts and rebuilds once. Owner chose to keep the CI link check strict.
- 08:30 rebuild refused: summary cited a story removed as a duplicate; orphan summary sentences are now dropped (gate unchanged).
- 21:28 retry refused to start: today's committed raw archive was left modified; the preflight now discards it.
- Denied hosts are pruned from a resumed draft too.
- GitHub's CI test list (198 tests) passes locally; full suite unchanged at 43 baseline failures.

## 2026-10-29: audit of the Oct 29 edition, quick fixes 1-5
The 04:30 run published on the first attempt (live 05:20, CI green). Audit findings and fixes (each with a regression test from the real headline):
1. "Renfe awards Siemens 30 trains" had no portfolio angle: `trains?` and `rolling stock` added.
2. Celebrity filter: "Waymo expands driverless ride service" matched the celebrity-ride pattern; "non-state actor" matched "actor". Both narrowed; the Pedrosa Cybercab ride is still dropped.
3. Seeking Alpha contributor blogs (`/instablog`) and Substack denied; "news round-up" and "X Explained:" / "What is ...?" headlines are not news.
4. A U.S. federal action in the headline (proclamation, directs, votes, approves, "about to" ...) is priority tier 1 and moves to Federal Policy when it fits that scope.
5. A summary sentence that opens with a bare "Embassy"/"Ministry"/"Cabinet"/"Parliament" is sent back to the writer (dropped if it persists).
Full suite: the same 43 failures as main. CI list passes. Replay: see docs/replay-diff-2026-10-29.md.

## 2026-10-29 (afternoon): round 2 of audit fixes (owner approved A-G)
Selection retry and second look when too few stories are picked, writer voice guidance (A); same-event merging across headlines via the verifier (B); incidental-topic check, opinion allowed (C); 25-story cap by priority (D); official federal source linked first (E); doctor reads the newest non-empty log (F); raw cron left alone (G, explained). Replays found and fixed: verifier budget too small (checks failed silently), a merged story dropped as a repeat despite a new death toll, FRA story not searched (tier 2, no .gov link in the article) -> agency-site search via SearXNG, duplicated outlets in Additional coverage. Final replay: 25 stories, gates passed (docs/replay-diff-2026-10-29-round2.md). CI list 228 OK; full suite the same 43 legacy failures as main (cleanup proposed as its own task). Not verified: the agency search live end to end (SearXNG upstreams rate-limited by my testing).

## 2026-10-29 (evening): legacy test failures cleared
Branch `fix/legacy-tests` (worktree `C:\newsroom\.wt\tests`), started after the 04:30 edition published. The 43 legacy failures are gone: full suite 348 tests, 0 failures. One real bug fixed: foreign war stories (Ukraine, Russia, Iran ...) were filed as Military UAS instead of International Security (`news_engine.INTERNATIONAL_MARKER_PATTERN`); the old tests are now its regressions. Other tests updated to the current section names, or deleted with their retired feature (Administration Wins, Headlines at a Glance, Streamlit, n8n); details in decisions.md. test_public_site had not run at all (import error) and hid six more stale tests. CI list gains test_news_engine, test_automated_briefing, test_public_site, test_master_briefing (292 tests, OK). Not done: no replay of an edition with the section fix; dead Administration Win code left in news_engine. Next: merge on the owner's go-ahead.

## 2026-10-29 (evening): the local newsroom dashboard
Owner asked for a live, private view of the process styled as a newsroom. Built `newsroom_live.py` (best-effort event feed to `.runtime/live/events.jsonl`), `newsroom_dashboard.py` (local server, http://127.0.0.1:8765, Server-Sent Events) and `dashboard/index.html` (eight desks from Wire Room to Delivery, live funnel, wire/fact-check log, the Notepad streaming the models' output as it is written, stamped decision cards, front page, the doctor's steps, improvements timeline, plant status and countdown; sound off by default). Hooks: feed searches, link resolution, every page fact-checked, every editor pick, every model call (Ollama stream), every QA event, the gates, the doctor, and the PowerShell publish steps; the 04:30 run opens the page. New task `newsroom-dashboard` at logon; the services check restarts it. Rehearsal (dry-run replay of Oct 29) streamed 4,781 events and passed with 25 stories. Tests: `tests/test_newsroom_live.py` (7) added to CI; CI list passes. Rule added to AGENTS.md: change the dashboard with the pipeline.

## 2026-10-30: the live site, first build (branch `feat/live-site`, worktree `C:\newsroom\.wt\live`)
Owner chose layout B (front page and river) with A (desk columns) as a view. Built, all new files:
`live_lead.py` (significance score and lead bar), `live_store.py` (SQLite store), `live_sources.py` (per-source clocks, conditional GETs, circuit breaker; 14 verified trade feeds + agency feeds + Federal Register + 71 Google News queries at one per 30 s), `live_desk.py` (fast lane: resolve, read with the edition's article rules, file, triage, first pass with the code check; slow lane: second-model check, score, lead desk), `live_doctor.py` (LIVE/LITE/HEADLINES/FROZEN, writer fallback, worker restarts, heartbeat), `live_feed.py` (public feed.json), `live_service.py` (runs it all; local preview on 8766), `site_assets/live/` (the page), `config/live.yaml`, `docs/live-site.md`. Dashboard: Plant panel shows the live site's level.
Tests: `tests/test_live_site.py` (35, no network or models) pass; `tests.test_newsroom_live` passes.
Real-network check: one full sweep queued 223 stories from 21 sources in 5m47s run one after another (the service runs them in a pool). FAA and DOT feeds answer 403 and the White House feed 404 (the edition has the same failures); the breaker rests them. The reading step ran on all 223 with models held back: 145 dropped by code (80 off-topic, 32 undated, 9 home pages ...), 78 read and filed. That check led to turning web search off for the live wire and filtering routine Federal Register notices.
Page checked in the browser at desktop and phone width with real headlines (headline-only, no model output yet).
Not verified: the model lanes against real Ollama (the 04:30 edition still held the GPU at 06:30, two hours in); the service running unattended; publishing anywhere public. Not done: a Task Scheduler entry and the services-check restart; merge to main (waiting for the edition to finish, so its working tree is not changed mid-run).

## 2026-10-30 (later): live page, owner's first review
Done on `feat/live-site`: FAA, DOT and White House now come from Google News `site:` searches (live wire and edition); first live run found 2 new FAA items in its first poll. Page: ticker under the masthead, slower one-source sweep line, green live dot, one full-width feed with category labels on the left and on/off toggles, market strip (Finnhub, every 5 min while NYSE is open), docket folded above the feed, no "verified" anywhere (labels "Quick summary" / "Summary double-checked"). Tests: 396 across all modules pass (test_live_site now 42).
The edition finished, so the service was started for real at 12:21 UTC (preview http://127.0.0.1:8766): first model calls ran (the writer's triage turned away an explainer). The preview only serves while `live_service.py run` is running; it is not scheduled yet.
First real model run (12:21-12:28 UTC) found three faults, all fixed with tests (403 pass): the fallback model tag was wrong and a writer outage dropped stories (54 lost, requeued); the 30 s stall limit failed calls during model swaps; unrecognized stories were filed as drone news (triage now picks the category). Also: market-research pages reached the page through Google links (deny list now checked after resolving). Service restarted on the fixed code; 105 stories requeued.
Afternoon: owner stopped GPU use for the newsroom (live service stopped, models unloaded; the morning edition stays scheduled). Second review done without models: lead needs a score of 50 even with no lead (a score-15 feature had led), Developing needs 40, docket under Developing, markets as a moving ticker under the wire, a tagline under the title. Tests 406 pass. Not verified: summaries and the second check end to end after the 12:40 fixes (needs the GPU).

## 2026-10-30 (afternoon): GPU back on; story times fixed
Owner allowed the GPU again. Service restarted 13:58 UTC: triage now returns a category, summaries and second checks land (Tesla Australia 15, Toyota/Waymo 25, Waymo picks bZ4X 46). Nothing reached 50, so there is no lead; the score-46 Waymo story sits in Developing as intended.
Owner asked why the newest stories said 12:00 p.m. Cause: when a page gives only a date, `parse_date` stamps noon Eastern, which also replaced a feed's real time and floated today's date-only stories above newer ones. Fix (`settle_published` in live_desk, cap in live_store): a bare date never replaces a feed time from the same day; no story is dated after we found it; date-only stories show "found 8:29 a.m." (or "no time given" for an earlier day). 13 stored stories repaired. Tests 412 pass (6 new). Checked in the browser.
Seen, not yet fixed: two outlets' versions of the Toyota/Waymo robotaxi story both appear (titles too different for the same-story check).

## 2026-10-30 (afternoon, later): the live site goes public
Owner asked: make it live at news.peterjwhite.org, a logo (chose the radar, animated), a /login page to force the
headline for 1-48 h (no visible button), "Checking the wire:" as a fixed label with faster cycling, more companies on
the markets ticker, a "more" at the bottom of the feed, and a shorter Developing/docket column.
Done: radar mark with a 4 s sweep and a contact that lights as the beam passes (still for reduced motion) plus
favicon; sweep line cycles the last 40 sources every 1.4 s; 12 live-only tickers (TSLA, UBER, AUR, MBLY, NSC, WAB,
AAL, LUV, AVAV, KTOS, EH, EVEX; the edition's list is unchanged); 25 stories then "Show 25 more" (feed.json holds
at most 300); Developing 3, docket 2 plus "N more". Override through GitHub issues (owner's choice of login
method) with `live_override.py`; feed delivery with `live_push.py` to the `live-feed` branch. Dashboard shows when
the public copy last went out and any owner override. Tests: 428 pass (test_live_site 73).
Merged feat/live-site into main (fast-forward after merging today's main in) and pushed; first live-feed push at
15:03 UTC. Seen: the fast lane is behind (472 stories waiting at 15:04); the service still runs only from this
session, so the public page shows "paused" when it stops.
Scheduled (owner: "Yes, schedule it"): task `newsroom-live` registered at logon from C:\newsroom and started 15:09 UTC; the store (SQLite backup, integrity ok), market snapshot and event log were copied from the worktree's runtime folder. The services check restarts it on a stale heartbeat (ran once: left the healthy service alone). Dashboard restarted to pick up the public-push status; it shows LIVE, public copy pushed under a minute ago. The live service now uses the GPU around the clock, pausing model work while the edition holds the lock.

## 2026-10-30 (afternoon, later): the live page takes the site root
Owner confirmed the override works (and ended it), then asked for the live page at news.peterjwhite.org. The live page is now the root; the morning edition moved to /edition/ (archive and search unchanged; every edition page's nav gains "Live" and "Latest" points to /edition/); /live/ redirects to the root. The two checks that read the root for the edition-id marker moved with it: `doctor.live_edition` and the 04:30 run's live check in `run_local_pipeline.ps1` now read /edition/. The build refuses to publish a site with no home page. Tests: 429 pass.

## 2026-10-30 (evening): the whole site moves to advancedtransportation.live
Owner bought advancedtransportation.live (Squarespace) and chose to move everything there (live page at the root, edition at /edition/, archive, search, /login/). Owner added GitHub's A/AAAA records, www CNAME and the GitHub Pages domain-verification TXT; all checked through dns.google. Code now names the new domain everywhere it named news.peterjwhite.org (canonical links, CNAME file, footers, doctor's edition check, the 04:30 run's live check, bot User-Agent, dashboard links); the repository's Pages domain was switched through the API. news.peterjwhite.org stops serving the site at the switch (GitHub cannot redirect an old custom domain); the owner can forward it at the DNS host.
Same evening: HTTPS certificate issued and enforced (http redirects). Owner removed the rulemaking docket, the tagline under the title and the footer's edition links; the live page is titled "Advanced Transportation Live" (the edition keeps "Advanced Transportation Update"). Impact words beside each scored story: Minor, Notable, Significant (owner rejected "Landmark" and "Major"). X feed: not built; X's read API is paid (about $200 a month), so the proposal is an "From official accounts" strip from agency/company RSS and Bluesky, awaiting the owner.
"From official sources" strip built (live_posts.py; 27 strip sources in config `posts`, plus the wire's own official sources). Federal transportation agencies are inactive on Bluesky; most items come from press feeds and site searches. Cloud AI costed for the owner (Claude Haiku 5.5 about $30-60 a month at today's volume; hybrid with the checker kept local about $20-45); not changed.
Evening, owner: "Stop GPU use for this for now until I tell you to resume." Task newsroom-live stopped and disabled, models unloaded; the services check now leaves a disabled live task alone. The public page shows "paused" once its feed is 25 minutes old. The morning edition's schedule is unchanged. To resume: Enable-ScheduledTask newsroom-live, then Start-ScheduledTask newsroom-live.
Evening, owner: "Okay begin again." GPU pause lifted: newsroom-live enabled and started 19:23 UTC on the same local models (no cloud, per "Stay local"). On start: 233 stories queued, oldest unchecked 98 min, level LITE until the checker catches up.
Evening, owner: "The eIPP announcement should be the headline now." The DOT/FAA release (Duffy marks eIPP milestone, Florida flights) had been dropped twice as undated: faa.gov refuses the bot, and the .gov "USA Banner" h1 hid the byline date and counted as a second headline. Extractor fixed (tests 441). The release was read by hand from faa.gov, passed the desk checks, got its first pass, and was requested as the lead for 12 h (issue #15, rejected: the override parser accepted only hex ids; widened to address-keyed ids, then re-sent).
Owner: "/login should do the login FIRST." The page now opens on a GitHub-token sign-in (owner account only, checked with GitHub) and files override requests itself.
Owner: "Come up with a way to get to the FAA and DOT browsers that is ethical, and don't lie." Reader now obeys robots.txt (tests 442). Access request drafted for the owner to send (docs/agency-access-request.md). Open: headline-only FAA/DOT links while blocked; Google News robots.txt (disallows /rss).
Owner: "Yes, headline-only." FAA/DOT releases refused by their sites now show as headline and link only (config desk.headline_only_hosts; dated by the news search, inside the freshness window). Tests pass.
Night, owner: "make it so the webpage only updates hourly from midnight to 4 a.m. (Eastern time) daily. Also, you can pause the GPU now until 4 a.m. (one time)." Public feed now pushes once an hour, on the hour, 00:00-04:00 ET (config push.overnight; the page waits 75 min before saying "paused" and tells readers it updates hourly). GPU paused from 22:30 ET with .runtime/live_site/gpu_pause.json until 2026-10-31 04:00 ET (08:00 UTC); writer unloaded; service at LITE, stories still read and posted as headlines; resumes by itself at 4 a.m., before the 04:30 edition. Tests 448.
Owner: "eliminate the policy and regulation category, and refit the ... articles." Policy desk removed (triage no longer offers it; the edition's policy sections are filed by their words, config topic_sections). The 43 stories on it refiled: the 38 shown by hand from their headlines (drones 19, av 8, robot 7, sec 3, air 2, rail 1), the 5 dropped ones by the word filer. Tests 449.
2026-10-31, owner asked for a design review of the live page (20 recommendations given in chat), then "Build Tier 1". Built: one entry per event with the other outlets listed (live_sort.py grouping, one writer call per story), a U.S. focus / Everything switch (stories marked U.S. or world by the same call), the wire bar limited to summarized U.S. stories, a compact lead after its first hour unless Significant or the owner's pick, story addresses and Copy link, desks and view in the address, share previews (og-card.png), a Pause button, and a check mark for the code check. Two corrections to the review: the page already honored the device's reduced-motion setting, and the wire bar already showed only stories on the page. Sorting the 490 stories already on the page goes newest first, about one story every 20-40 s beside first passes, so older stories group over a few hours. Tests 455.
