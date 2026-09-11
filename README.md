# Advanced Transportation News Update

Local-first daily news production for [news.peterjwhite.org](https://news.peterjwhite.org). Python collects and validates public information, Ollama performs the editorial passes, n8n orchestrates the daily run, and GitHub Pages serves the generated archive.

Normal production makes **zero OpenAI API calls**. The defaults are `AI_PROVIDER=ollama` and `OPENAI_FALLBACK_ENABLED=false`; an existing OpenAI key is ignored. The legacy cloud build requires a manual `USE OPENAI` confirmation.

## Architecture

```text
Google News/RSS + official feeds + Federal Register + local SearXNG + Ette email
                                  |
                                  v
                     Python collection and cleanup
                                  |
                                  v
                Ollama structured editorial generation
                                  |
                                  v
                 deterministic publication validation
                                  |
                                  v
        briefing JSON -> git push -> GitHub Pages deployment
```

Python remains the processing engine. n8n only triggers the Gmail and weekday-fallback paths, launches the guarded PowerShell runner, and records the concise command result.

## Local requirements

- Windows, Git, Python 3.12, and PowerShell 7
- Ollama at `http://127.0.0.1:11434`
- `qwen3.6:27b-q4_K_M` (`ollama pull qwen3.6:27b-q4_K_M`)
- n8n 2.38.7 or compatible at `http://localhost:5678`
- WSL Ubuntu for the loopback-only SearXNG service

Create the application environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Load the values from `.env` in the process that runs the application. Keep `OPENAI_FALLBACK_ENABLED=false` for production.

## Commands

```powershell
# Provider and real structured-generation health check
.\.venv\Scripts\python.exe automated_briefing.py --health-check

# Complete manual build with an exported supplemental email
.\.venv\Scripts\python.exe automated_briefing.py --supplemental-file C:\private\email.txt

# Build and validate without writing publication JSON
.\.venv\Scripts\python.exe automated_briefing.py --supplemental-file C:\private\email.txt --dry-run

# Validate an existing edition
.\.venv\Scripts\python.exe automated_briefing.py --validate-only data\latest_briefing.json

# Real archived-corpus benchmark
.\.venv\Scripts\python.exe benchmark_models.py --models qwen3.6:27b-q4_K_M mistral:latest llama3:latest --output benchmark_results\latest.json

# Guarded end-to-end collection, build, commit, push, and live verification
.\scripts\run_local_pipeline.ps1
```

The production runner refuses a dirty repository, pulls `main` with `--ff-only`, checks Ollama, collects fresh public-source candidates, validates before saving, stages only generated data, pushes without force, and verifies that the dated edition reached the live site. A failed validation never replaces the prior live edition.

## Web discovery

`daily_update.py` combines the existing targeted Google News/RSS collection, Federal Register data, direct official feeds, and local SearXNG results. Article metadata retrieval resolves redirects, improves headlines/snippets where accessible, normalizes tracking parameters, corrects `msn.om`, and retains discovery metadata when a page blocks access. A SearXNG outage is reported but does not stop the independent discovery channels.

Install or update SearXNG:

```powershell
wsl -d Ubuntu -- bash scripts/setup_searxng_wsl.sh
curl.exe "http://127.0.0.1:8080/search?q=FAA+BVLOS&format=json"
```

The service binds only to `127.0.0.1:8080` and starts through the Ubuntu user systemd instance.

## n8n

Start n8n with `scripts/start_n8n.ps1`, then open [http://localhost:5678](http://localhost:5678). Import `n8n/workflows/transportation-news-local.json` if it is not already present.

In n8n:

1. Create or select a Gmail OAuth credential on **Supplemental Gmail**. This must be done by the owner.
2. Confirm the trigger query identifies mail from `ette0937@yahoo.com` with subject `9/11/26` or adjust the subject portion for the continuing daily format.
3. Test **Manual test** first. It performs a feed-only run.
4. Test the Gmail path with a non-production copy before activating it.
5. Activate the workflow only after the local repository is clean and on `main`.

The Gmail trigger writes the body to an ignored private runtime file, invokes the runner with only that path, and deletes the file afterward. The body is never interpolated into shell code. The 1:00 p.m. Eastern weekday fallback performs a feed-only build only when no successful publication has completed that day. Gmail-triggered and fallback runs share a persisted Eastern-date success guard, and production execution concurrency is limited to one so later same-day triggers do not republish. n8n success and error execution payloads are disabled so the email body is not retained in execution history. The runner has bounded retries and emits only operational counts/timing, git SHA, and deployment status.

## Owner editor

The preferred editor is local:

```powershell
$env:OWNER_PASSWORD = '<choose a long local password>'
$env:SESSION_SECRET = '<generate a separate random value of at least 32 characters>'
.\.venv\Scripts\python.exe owner_portal\local_server.py
```

Open `http://127.0.0.1:8765`. The server rejects non-loopback binding, requires signed sessions and same-origin writes, validates the edition, atomically updates both JSON files, commits with `Owner edit news edition `, and pushes through local Git credentials. That exact commit prefix triggers `.github/workflows/publish-owner-edits.yml` to validate and redeploy Pages.

## Recovery

- Ollama failure: run `--health-check`; verify `ollama list` and the configured model. Production does not fall back silently.
- SearXNG failure: `wsl -d Ubuntu -- systemctl --user restart searxng.service`. Collection still uses RSS and official sources.
- n8n unreachable: run `scripts/start_n8n.ps1`, then check `http://localhost:5678/healthz`.
- Dirty repository: inspect `git status`; preserve or commit intentional work. The production runner will not discard it.
- Push failure after commit: the local commit is preserved. Fetch, reconcile without force-pushing, and rerun validation before pushing.
- Deployment delay: inspect the GitHub Pages Actions run. The JSON commit remains the source of truth and the prior site stays available until deployment succeeds.

## Optional OpenAI emergency path

OpenAI is not required. To opt in deliberately, set `AI_PROVIDER=openai`, provide `OPENAI_API_KEY`, and invoke a manual build, or run the **Emergency OpenAI Briefing Build** workflow and type `USE OPENAI`. Merely setting an API key does nothing while `AI_PROVIDER=ollama` and `OPENAI_FALLBACK_ENABLED=false`.

The visible title must remain **Advanced Transportation News Update** and the footer must remain exactly: **Public source, AI-assisted news update.**
