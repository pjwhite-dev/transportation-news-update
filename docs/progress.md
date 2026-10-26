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
