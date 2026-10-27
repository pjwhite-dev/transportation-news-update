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
