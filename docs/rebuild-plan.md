# Rebuild brief: news.peterjwhite.org

You are taking over the rebuild of my advanced transportation news site.
Repository: https://github.com/pjwhite-dev/transportation-news-update
Live site: https://news.peterjwhite.org (GitHub Pages)
This brief is the plan. Work through it autonomously, phase by phase, starting with Section 1 and then Phase 0.

## 1. First actions in this session
1. Save this entire brief verbatim to docs/rebuild-plan.md.
2. Move the useful editorial rules in the current AGENTS.md (section placement, relevance exclusions, supplemental-link accounting) into docs/editorial-rules-legacy.md. Then rewrite AGENTS.md and create CLAUDE.md so both state the operating rules in Section 3 and point to docs/rebuild-plan.md. The current AGENTS.md describes an obsolete OpenAI/GitHub Actions design.
3. Create docs/progress.md (append a dated entry at the end of every work session: what changed, tests run, what is next) and docs/decisions.md (one line per judgment call you make on my behalf, with the reason).
4. Write .claude/settings.json permission rules. Allow: python, pip and pytest in the repo venv; git except force-push and history rewrites; gh; ollama; nvidia-smi; schtasks queries; the repo's PowerShell scripts. Deny: git push --force, git push -f, git filter-*, any deletion under data/archive or data/raw_archive. After the move to C:\newsroom, also deny reads of my OneDrive, Documents, Desktop, Downloads and browser profile folders. Until then, stay inside the repository folder as a rule of conduct. If the app will not let a rule be allowlisted, list it for me once.

## 2. Context
- Hardware: Windows workstation, RTX 4090 (24 GB), Threadripper PRO 5955WX (8 memory channels), 64 GB RAM.
- Installed: Ollama, Hermes Agent (Nous Research), n8n, Python, PowerShell, Git, WSL Ubuntu running SearXNG on 127.0.0.1:8080.
- The repository currently lives in OneDrive (C:\Users\pjwhi\OneDrive\Documents\ChatGPT\Advanced Transportation Daily\). It moves to C:\newsroom\ in Phase 1.
- My priorities, in order: (1) editorial quality and factual accuracy; (2) reliability, automatic recovery, no silent failures; (3) comprehensive, timely coverage; (4) minimal human involvement; (5) efficient use of local hardware; (6) maintainability and visibility; (7) low recurring cost.
- Out of scope: my separate ChatGPT email newsletter. Do not touch it or ingest its editions.

## 3. Operating rules
- Work autonomously. Do not ask me to approve routine work. Decide, and log the decision in docs/decisions.md.
- Run everything under my own Windows account. Do not create a separate Windows user.
- Every change goes on a branch. Merge to main automatically (fast-forward or squash) when the full test suite and the validation gates pass. Push without force.
- Never: force-push or rewrite main's history; delete or rewrite past editions in data/archive or data/raw_archive; weaken, skip or disable a validation gate to make a run pass; print, log or commit secrets; read or write outside the repository, C:\newsroom, and the config folders of Ollama, Hermes, Task Scheduler and NSSM; send data to any endpoint not named in this brief; modify the email newsletter.
- Ask me only for: (a) credentials, (b) actions that need administrator rights or BIOS access, (c) the Phase 2 blind judging, (d) genuine blockers. Batch requests into one message per phase, with exact steps.
- Credentials: never ask me to paste a secret into chat. Write scripts/set_secret.py, which prompts with getpass and stores the value with the Python keyring package (Windows Credential Manager). Code reads secrets only from keyring. Remove secrets from any .env file inside the repository.
- Keep changes small and tested. Anything with one right answer (fetching, deduplication, tracker status, validation, git, deployment) is deterministic code, never model judgment.
- A failure that recurs twice gets a new deterministic recovery rule, not a cleverer prompt.
- Budget: cloud model spend at most $50 a month. Record tokens and cost per run in the ledger. If the month's spend reaches $45, stop calling cloud models, fall back to local, and alert me.

## 4. What my review found (repository at commit a3033fc, October 23, 2026)
1. Three uncoordinated pipelines:
   - The GitHub Actions raw collector commits data/latest_raw_news.json to main daily.
   - n8n runs scripts/run_local_pipeline.ps1; it last published October 5.
   - A Hermes path whose code is not in the repository: "Hermes source monitor", deterministic selection plus llama3.3:70b prose, commit label "Publish Hermes news edition". It does not read the raw feed.
2. Only 9 of the 20 weekdays from September 28 to October 23 published. October 6–14 were all missed, and October 23 is missing.
3. On October 21–22, the Executive Summary, What to Watch, and about 20 article summaries ("Source headline: ...") are deterministic fallback templates. The JSON still credits the 70B model and says validation_status "passed".
4. llama3.3 70B at Q4 plus a 32K context needs about 53 GB. OLLAMA_TIMEOUT_SECONDS=300 with non-streaming requests almost guarantees timeouts, then the fallback.
5. ai_providers.py forces every call, including prose, through format: <JSON schema> with think: false.
6. validate_complete_briefing() checks almost nothing editorial: no template, duplicate, relevance, edition-date or RIN checks.
7. regulatory_tracker.py has 8 hard-coded entries "verified July 15, 2026" and lacks RINs 2127-AM70, AM71, AM72, AN00, AM57 and 2126-AC17. data/archive/2026-10-20.json has all 10; use it as the seed.
8. Duplicates and off-topic stories pass. October 22 carried the border drone restriction six times, Beta's authorization four times, and Hegseth–Guyana twice. It filed the 737 MAX-10 certification and an air traffic controller pay deal under UAS.
9. 384 of the 392 raw links on October 23 are news.google.com redirect wrappers, mostly with empty summaries.
10. Push race: the bot and the local runner both commit latest_raw_news.json to main. One rejected push makes every later run fail at git pull --ff-only.
11. The collector's 4:15 a.m. ET schedule actually landed commits between 8:13 a.m. and 1:57 p.m. ET.
12. The repository sits in OneDrive. Nothing restarts n8n, Ollama, Hermes or SearXNG after a reboot. The Hermes gateway's default Windows task starts at logon, not at boot.
13. The n8n path feeds an inbound email (sender ette0937@yahoo.com) into the model with only a From-header check.

## 5. Target architecture
- One deterministic Python runner (package newsroom/) owns triggers, job state and publishing.
  - State lives in a SQLite run ledger (runs/ledger.db): one row per stage attempt with status (queued, running, succeeded, failed, retrying, blocked), attempt number, timestamps, heartbeat, error class, model, tokens and cost.
  - Outputs go to runs/{date}/{attempt}/. Stages are idempotent and cached by content hash, so a crash resumes from the last completed stage.
- Stages: collect, resolve, cluster, screen, extract, tracker, select, write, summarize, watch, critique, revise, gate, build, commit, deploy, verify.
- Windows Task Scheduler triggers the runner. Hermes is not the scheduler. n8n is retired.
- Hermes profiles:
  - operator: Telegram commands; read-only tools plus the runner's command allowlist; model Claude Haiku 5.5 with a local fallback.
  - analyst (optional, Phase 4): researches thinly sourced but consequential stories with read-only web tools; returns fact sheets, never reader-facing prose.
  - repair: diagnoses failures in git worktrees and writes minimal fixes plus regression tests.
- A Hermes Kanban board, newsroom-ops, holds repair cases only: Manual orchestration, max_in_progress 1. Production Hermes skills live in ops/hermes/skills/; disable agent self-editing of them.
- Alerting: the runner sends Telegram alerts directly through the Bot API, so alerts work when Hermes is down. Healthchecks.io dead-man checks alarm from outside when the PC goes quiet.
- GitHub Actions re-validates and deploys Pages. The emergency OpenAI workflow becomes a provider-neutral cloud fallback build.

## 6. Phases and exit gates

### Phase 0: Stop the bleeding (start now; days 1–3)
1. Find the October 23 cause in the current OneDrive checkout before changing anything:
   - git status -sb and git log --oneline origin/main..HEAD. An unpushed "Publish Hermes news edition 2026-10-23" commit means the push was rejected after the bot's 9:28 a.m. commit. Also look for OneDrive conflict copies.
   - System event log, October 22 6 p.m. to October 23 noon: event IDs 1074, 6005, 6006, 6008 and 41, and WindowsUpdateClient 19, 20 and 43. Check Reliability Monitor for python.exe or ollama.exe crashes.
   - Hermes: hermes gateway status, hermes cron list, hermes sessions; logs under %LOCALAPPDATA%\hermes\ (or ~/.hermes in WSL); schtasks /Query /TN Hermes_Gateway /V /FO LIST. Locate the script behind "Hermes source monitor" and its output folder.
   - Ollama: %LOCALAPPDATA%\Ollama\server.log and rotated logs, 4 a.m. to noon. Look for llama3.3 request durations, "client connection closed", CUDA allocation failures and "llama runner process has terminated".
   - Also check: the TaskScheduler Operational log; the n8n database under C:\Users\pjwhi\.n8n\ (error executions, and whether the 1 p.m. fallback fired); leftovers in .runtime\; gh run list --limit 20.
   - Write docs/incidents/2026-10-23.md with a minute-by-minute timeline and the confirmed cause.
2. Copy the Hermes edition script and its source list (excluding any secrets) into legacy/hermes_path/. Then disable the Hermes edition cron job and the n8n workflow's schedule triggers. From here on there is exactly one publisher.
3. Interim publisher: the existing automated_briefing.py path with these changes:
   - qwen3.6:27b-q4_K_M with streaming responses.
   - A 120-second no-token stall timeout instead of the 300-second wall-clock timeout.
   - Free-text (not schema-constrained) generation for prose fields.
   - Once the Anthropic key exists, Claude Opus 5.5 (claude-opus-5-5) writes the Executive Summary.
   - Remove llama3.3:70b from the configuration.
4. Make fallback text a hard failure. No template Executive Summary, "Source headline:" summary, or "Watch for follow-up developments" item may ever be saved as an edition. If prose cannot be produced, the run fails and alerts me.
5. Add these hard gates from Section 7.3 to validation and to .github/workflows/publish-news-site.yml, before the build: template text, headline echo, duplicates, links, tracker, freshness, provenance.
6. Move the cloud collector off main: daily-news-update.yml uploads an artifact or commits to a raw-feed branch. Only the local publisher writes edition data to main.
7. Restore the 10-entry tracker (seed from data/archive/2026-10-20.json), confirmed by the Section 7.2 fetchers.
8. Reliability basics:
   - Ollama, the Hermes gateway and SearXNG start at boot (Section 7.9).
   - Healthchecks.io edition-verified and pc-alive checks (Section 7.7).
   - Direct Telegram alerts from the runner.
9. Publish the next edition through the fixed path and verify it live.
Exit gate: no edition containing fallback text can publish, and every missed or failed edition produces a Telegram alert by 8:00 a.m. ET.

### Phase 1: Foundation (weeks 1–2)
1. Clone the repository to C:\newsroom\ under my account and point every task and service at it. Leave the OneDrive copy untouched but unused.
2. Build the runner, ledger and CLI (python -m newsroom run | resume | status | rerun <stage> | hold | release | rollback <date>). Wrap today's stages first, then replace run_local_pipeline.ps1 and check_publication_state.py.
3. Resolve Google News redirects to publisher URLs and fetch article text, respecting robots rules and paywalls and keeping metadata when blocked. Anchor coverage windows to the previous edition's cutoff; Monday spans the weekend.
4. Rebuild the tracker per Section 7.2.
5. Build the edition manifest and verifier per Section 7.8. public_site.py emits <meta name="edition-id"> and edition.json.
6. Commit anything useful from legacy/hermes_path into the new code; finalize AGENTS.md and CLAUDE.md for the new system.
7. Supplemental email: check whether the ette0937@yahoo.com trigger has received mail in the last 30 days.
   - If yes, re-implement intake in Python through the Gmail API. Verify DKIM and SPF results and treat the body as untrusted data.
   - If no, drop it and log the decision.
Exit gate: the runner publishes five consecutive weekday editions on time, and n8n is deactivated.

### Phase 2: Editorial rebuild (weeks 3–5)
1. Implement Section 7.1.
2. Pull candidate models and verify the tags exist: qwen3.8:27b (Q4_K_M and Q5_K_M) and gemma4:31b. Keep qwen3.6:27b as the fallback. Optional: gpt-oss-120b through llama.cpp llama-server with --n-cpu-moe, if memory allows.
3. Run the blind benchmark in Section 7.10 over 15 archived days, including October 21 and 22.
   - Build a small local judging page and batch my judging into one session of about 30 minutes. Message me when it is ready.
   - If I have not judged within 3 days, decide with the two model judges and log it.
4. Shadow mode: two weeks of daily dry-run editions on a staging site (a second Pages repository, or a local preview over Tailscale). Include the staging link in the daily Telegram summary. The interim publisher keeps publishing meanwhile.
Exit gate (public cutover): benchmark acceptance criteria met and shadow editions consistently strong. Log the evidence and cut over without waiting for me unless the critic or the gates show a regression.

### Phase 3: Cutover and burn-in (weeks 6–9)
1. The new pipeline publishes. Run a 20-weekday burn-in with the fault drills in Section 7.10.
2. Hermes operator commands and the local dashboard (served on 127.0.0.1, reading the ledger) go live.
3. Repair Agent goes live with no blocking approvals:
   - It auto-merges its fix when the full suite and a dry-run edition pass and the diff is at most 150 lines.
   - Otherwise it opens a PR and notifies me, and the last good edition stays live.
Exit gate: every production-ready criterion in Section 7.10 is met.

### Phase 4: Autonomy and cleanup (week 10 onward)
- Tier-1 policies on.
- Cloud fallback auto-dispatches at 9:00 a.m. ET, through a Healthchecks.io webhook, when no verified edition exists.
- Optional analyst profile.
- Cleanup:
  - Remove the n8n workflow and start_n8n.ps1.
  - Move streamlit_app.py and .streamlit/ to an archive branch.
  - Check whether owner_portal is deployed publicly on Vercel; if it is, remove that deployment configuration.
- Weekly reliability and cost digest to Telegram.

## 7. Specifications

### 7.1 Editorial chain
- Cluster candidates into events using embeddings plus URL and title rules. One event is one story, with supporting links.
- Build a fact sheet per cluster:
  - actor, action, date, key numbers;
  - primary link (prefer the Federal Register, agency releases and company filings) and supporting links;
  - status class: completed operation, regulatory action, award, test or demonstration, announced plan, proposal, research, or opinion;
  - a flag for whether it is new since the last 10 editions.
- Score significance with explicit points for federal action, first-of-kind operations, safety or security effect, scale and U.S. relevance. Subtract for press releases with no event, market reports, opinion and stale news.
- Select (cloud model): from the top 25 fact sheets plus yesterday's summary, choose 3–5 lead developments and say why each matters. Name a trend only when at least two fact sheets support it.
- Executive Summary (cloud model):
  - 220–350 words in 3–4 short paragraphs.
  - The first sentence names the most consequential development and its consequence.
  - Every claim is linked inline.
  - Verbs match the status class ("issued", "proposed", "announced plans to").
  - No workflow language and no headline lists.
- Article summaries (local model, free text): 2–3 sentences, 60 words at most. First what happened (actor, action, number), then why it matters or what comes next, if the source supports it. Reject any summary that restates its headline.
- What to Watch: only items with a date or a named trigger, each with a link (deadlines, votes, scheduled flights).
- Critic (a different model family from the writer):
  - Scores 1–5 on accuracy, status discipline, lead selection, significance, synthesis, specificity, style and novelty.
  - Blocking defects: any unsupported claim, status mismatch, unsupported trend, workflow language, or any dimension below 3.
  - Pass means no blocking defects and an average of at least 4.0.
- Revise at most twice, then try the fallback writer once.
  - A style-only failure after that publishes, with the defect logged.
  - An accuracy or status failure never publishes; the last good edition stays live and I get an alert.
- House style file editorial/house_style.md:
  - Active voice; agency names on first reference; numerals; executive orders by official title in italics, with the number in a note.
  - Banned phrases: "covers", "landmark", "game-changer", "poised to", "underscores", "amid", "navigating the landscape", "it remains to be seen", "Source headline", "Watch for follow-up developments".
  - Start the exemplars from the October 16 and October 20 Executive Summaries in data/archive.
- Keep the live site's current section set, visible title and footer unless I say otherwise.

### 7.2 Regulatory tracker
- Watch list tracker/rulemakings.yaml, with required: true for:
  - FAA Part 108 / BVLOS, 2120-AL82
  - TSA BVLOS security, 1652-AA80
  - FAA Section 2209 / UAFR, 2120-AL33
  - FAA civil supersonic flight, 2120-AM15
  - NHTSA FMVSS lighting, 2127-AM70
  - NHTSA visibility / defrost / wiper, 2127-AM71
  - NHTSA transmission control, 2127-AM72
  - NHTSA braking, 2127-AN00
  - NHTSA temporary exemptions, 2127-AM57
  - FMCSA automated commercial motor vehicles, 2126-AC17
- Sources:
  - Federal Register API: keyless; filter with conditions[regulation_id_number] and conditions[docket_id]; also check public inspection.
  - Regulations.gov API v4 (api.data.gov key).
  - Reginfo.gov for OIRA review status and the Unified Agenda.
  - Agency sites as supporting sources only.
- Status rules:
  - Keep an append-only event ledger and derive status from it by fixed rules (comment period open or closed).
  - Show "final rule" only when a Federal Register RULE document exists for that RIN.
  - Show Unified Agenda dates separately, labeled "Agency projection (Unified Agenda, <edition>)".
- Gates:
  - All 10 required entries present, each with an official source link that returns HTTP 200 from an official domain.
  - Each verified within 24 hours; otherwise show "verification delayed" and warn.
  - No final status without a RULE document, and every date traceable to the event ledger.
  - A day-over-day diff feeds Executive Summary candidates.
- A model may only phrase the one-line "what changed" from the official abstract.

### 7.3 Hard gates (run locally and in CI)
- Freshness: the edition date is today in ET, and the window starts at the previous edition's cutoff.
- Template text: no known fallback patterns anywhere.
- Headline echo: no summary has more than 80 percent token overlap with its headline.
- Duplicates: no shared cluster ID, canonical URL or near-identical title.
- Relevance: every story has a category and a status class.
- Links: HTTP 200 publisher or official URLs, with no aggregator redirect wrappers.
- Tracker: everything in Section 7.2.
- Executive Summary shape: 220–350 words, at least 3 inline links, no banned phrases or workflow language.
- Provenance: every model-written field records its model, and no fallback text exists.
- Schema and size: valid schema; no empty required section when candidates existed; file sizes in normal range.
- Site build: the rendered page contains the edition date and the summary text.
Replace validation_status with a validation report listing each check, its result and the evidence. Keep a regression set of known-bad editions (October 21, October 22, plus synthetic corruptions) that must always fail.

### 7.4 Recovery (deterministic first)
- Ollama down: probe /api/version plus a 5-token generation, restart the service, retry, then switch to the fallback model. Escalate after 2 failed restarts.
- Stall: no token for 120 seconds; abort, retry with a smaller batch, then the fallback model.
- Malformed JSON: re-ask with the validation error twice, split the batch, then mark the story unprocessed (never use a template).
- Source outage: back off and continue with other sources; record the error. Alert if the Federal Register is unreachable for 24 hours.
- Junk flood: cap stories per cluster and rely on screening. Alert if counts are 3 standard deviations from normal.
- Python exception: retry once for transient error classes. The same exception twice opens a repair case.
- Dead process: a heartbeat older than 10 minutes triggers a resume.
- Missed schedule: the 15-minute ensure task starts the run late.
- Git conflict: regenerate generated files on top of remote main; never force-push.
- Deploy failure: re-dispatch the workflow twice.
- Stale live site: re-dispatch the deploy; alert after 20 minutes.
- Deadlines: target publication 7:00 a.m. ET, alert at 8:00, cloud fallback at 9:00.

### 7.5 Models and GPU
- Local models:
  - qwen3.8:27b for screening, extraction with thinking on, and summaries if it wins the benchmark.
  - gemma4:31b as a summary candidate.
  - A small local embedding model for clustering.
  - qwen3.6:27b as the fallback.
- Cloud models through the Anthropic API: Claude Opus 5.5 (claude-opus-5-5) as selector and writer; Claude Haiku 5.5 (claude-haiku-5-5) as the operator.
- Critic: local qwen3.8:27b with thinking on, a different family from the writer. If I later add an OpenAI key, benchmark OpenAI's current mid-tier model as the critic.
- Ollama settings: OLLAMA_HOST=127.0.0.1, OLLAMA_FLASH_ATTENTION=1, OLLAMA_KV_CACHE_TYPE=q8_0, OLLAMA_MAX_LOADED_MODELS=2. OLLAMA_NUM_PARALLEL=1 for writing, up to 4 for short classification calls. 32K context by default.
- Scheduling: batch work by model; run network I/O concurrently with GPU work; editions take priority over discovery.
- Check whether the 64 GB occupies all 8 memory channels and note the result in docs/progress.md.

### 7.6 Telegram
- The runner sends alerts directly through the Bot API; Hermes consumes incoming messages on the same bot.
- Accept messages only from my user ID, in a direct chat.
- Message classes: critical (with sound); routine success (one silent message a day); weekly digest (silent, Mondays).
- Commands through the Hermes operator: /status, /edition, /why <story>, /correct <story> <text>, /rerun <stage>, /hold, /release, /rollback <date>, /fallback, /quiet <hours>. Consequential commands execute directly, followed by a one-line statement of what was done.
- Redact tokens, keys, user-profile paths and email addresses from all outbound text. Nothing nonpublic ever goes through Telegram.

### 7.7 Healthchecks.io
- pc-alive: pinged every 5 minutes; alert after 30 minutes of silence.
- edition-verified: pinged by the verifier; weekdays; alert if there is no ping by 8:00 a.m. ET.
- discovery: alert after 45 minutes of silence.
- tracker-verified: alert after 26 hours.
- Use the Telegram integration. Add a free HTTP uptime monitor on the site itself.

### 7.8 Publication
- One atomic commit per edition containing:
  - data/latest_briefing.json
  - data/archive/{date}.json
  - data/editions/{date}/manifest.json, recording code SHA, prompt and style hashes, the model per field, tokens, cost, every check result, critic scores, repairs and fallbacks, and per-source counts.
- CI re-runs the gates before deploying.
- The verifier fetches the live page and edition.json with cache-busting and compares them with the manifest before pinging edition-verified.
- Rollback is git revert plus a redeploy. Corrections are a new revision with a visible note.
- Failed attempts stay in runs/ for 90 days and never reach main.

### 7.9 Windows setup
- BIOS: power on after an outage (put this on my admin list).
- Windows Update active hours: 2:00 a.m. to 8:00 p.m.
- NSSM services under my account for ollama serve and the Hermes gateway. Disable the Ollama tray app's auto-start.
- A startup task that launches the WSL distribution, with lingering enabled so SearXNG starts.
- Scheduled tasks:
  - newsroom-edition: weekdays at 4:30 a.m.
  - newsroom-ensure: every 15 minutes.
  - newsroom-discovery: every 5 minutes, but Google News queries only every 15–30 minutes.
  - newsroom-heartbeat: every 5 minutes.
- Each task: run whether or not I am signed in (I will enter my Windows password when prompted); start as soon as possible after a missed run; restart 3 times on failure; never run in parallel with itself; wake the computer.
- Power plan: never sleep. Pin the current NVIDIA driver.
- Bind Ollama, SearXNG, the Hermes dashboard and the runner dashboard to 127.0.0.1.

### 7.10 Tests, benchmark, drills and criteria
- Tests:
  - Unit tests for every gate, tracker rule, URL resolver and clustering rule.
  - Record-and-replay integration tests.
  - Replay the deterministic stages over all ~100 archived days; reruns must give identical output, and the known-bad set must fail.
- Benchmark:
  - 15 archived days, with identical fact sheets per day for every candidate.
  - Candidates: the cloud writer(s) and the best local model. Baseline: the summary actually published that day.
  - Blind pairwise judging by me, plus two model judges.
  - Pick by my win rate, then claim-support rate, status errors, cost and latency.
  - Acceptance: at least a 70 percent win rate against the published summary; at least 98 percent of sentences supported by a fact sheet; zero status errors; the critic rejects every known-bad edition.
- Drills:
  - Kill Ollama mid-run.
  - Stall the response stream.
  - Return malformed JSON.
  - Block the Federal Register with a hosts-file entry.
  - Force a push conflict.
  - Make a deploy fail.
  - Leave the live site stale.
  - Reboot at 5 a.m.
  - Leave the PC off all morning.
  - Stop Hermes.
  - Revoke an API key.
  - Fill the disk to 95 percent.
  - Replay Monday, November 2, 2026, across the daylight saving change.
- Production-ready means, over 20 weekdays:
  - every edition verified live by 7:30 a.m. ET, and at least 19 by 7:00;
  - zero silent failures;
  - every deterministic drill recovers within 30 minutes unattended;
  - I rate at least 16 of 20 editions 4 or 5 out of 5;
  - no corrections to any regulatory status or date;
  - tracker at 10 of 10 every day;
  - zero duplicates and at most one off-topic story per edition;
  - at most one non-actionable alert per week;
  - spend within the cost cap.

## 8. What I will provide when you ask (once, batched)
- Anthropic API key.
- Telegram bot token from @BotFather and my numeric user ID.
- Healthchecks.io ping URLs.
- GitHub fine-grained token for this repository only, with contents and actions write access.
- Optional Regulations.gov (api.data.gov) key.
- My Windows password, when Windows prompts during task and service setup.
- The BIOS change.

Begin with Section 1, then Phase 0.
