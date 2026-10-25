# Phase 0 requests (one batch)

Everything below needs you; nothing else is blocking. Order matters.

## A. Credentials (never paste into chat). From the repo folder, one command each:

    .venv\Scripts\python.exe scripts\set_secret.py <name>

Each prompts with hidden input and stores the value in Windows Credential Manager.

| name | where to get it | needed for |
|---|---|---|
| telegram_bot_token | @BotFather -> /newbot | alerts (critical), daily summary |
| telegram_chat_id | message @userinfobot; your numeric user ID | alerts go only to you |
| hc_edition_verified | Healthchecks.io check "edition-verified" (weekdays, due 8:00 a.m. ET); copy its ping URL | outside dead-man alarm |
| hc_pc_alive | check "pc-alive" (every 5 min, grace 30 min) | PC-offline alarm |
| hc_tracker_verified | check "tracker-verified" (26 h) | tracker freshness alarm |
| hc_discovery | check "discovery" (45 min) | later (Phase 1) |
| anthropic_api_key | console.anthropic.com | Claude Opus 5.5 writes the Executive Summary (not wired yet; local model is used until then) |
| github_token | fine-grained token, this repo only, contents + actions write | deploy re-dispatch / verification |
| regulations_gov_api_key (optional) | api.data.gov | docket details (tracker works without it) |

Healthchecks.io: connect its Telegram integration to the same bot so a silent PC still reaches you.

## B. Administrator actions (elevated PowerShell, once)

1. `winget install NSSM.NSSM`, then reopen PowerShell.
2. `powershell -ExecutionPolicy Bypass -File scripts\windows\install_services.ps1`
   (installs Ollama + Hermes gateway as boot services, keeps WSL/SearXNG alive at boot, never-sleep power
   plan, Windows Update active hours 2:00-20:00; asks your Windows password once).
3. `wsl -d Ubuntu -- sudo loginctl enable-linger $USER`
4. `powershell -ExecutionPolicy Bypass -File scripts\windows\install_tasks.ps1`
   (newsroom-edition 4:30 weekdays, newsroom-ensure every 15 min, newsroom-heartbeat every 5 min,
   newsroom-tracker 4:00 daily; asks your Windows password once).
5. BIOS: set "Restore on AC Power Loss" / "Power On after power failure" so the PC comes back by itself.
6. Install the GitHub CLI (`winget install GitHub.cli`) so Actions runs can be listed locally (optional).
