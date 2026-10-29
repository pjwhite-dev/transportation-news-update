# Runs every 5 minutes (task newsroom-services-check): if Ollama, the Hermes gateway or the
# WSL keep-alive has stopped, start its logon task again. Never restarts a healthy service.
$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$runtime = Join-Path $repoRoot ".runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$log = Join-Path $runtime "services-check.log"

function Note([string]$message) {
    Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "s"), $message)
}

function Ollama-Alive {
    try { (Invoke-WebRequest "http://127.0.0.1:11434/api/version" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}

if (-not (Ollama-Alive)) {
    Note "Ollama not answering; starting newsroom-ollama."
    Start-ScheduledTask -TaskName "newsroom-ollama" -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 20
    if (-not (Ollama-Alive)) {
        Note "Ollama still down after restart."
        & $python (Join-Path $repoRoot "alerts.py") critical "Ollama is not answering and the restart did not bring it back." | Out-Null
    }
}

if (-not (Get-Process -Name hermes -ErrorAction SilentlyContinue)) {
    Note "Hermes gateway not running; starting newsroom-hermes-gateway."
    Start-ScheduledTask -TaskName "newsroom-hermes-gateway" -ErrorAction SilentlyContinue
}

# wsl.exe writes UTF-16, so strip the NUL bytes before matching.
$running = ((wsl.exe -l --running 2>$null | Out-String) -replace [char]0, "")
if ($running -notmatch "Ubuntu") {
    Note "WSL not running; starting newsroom-wsl."
    Start-ScheduledTask -TaskName "newsroom-wsl" -ErrorAction SilentlyContinue
}

# The local newsroom dashboard (http://127.0.0.1:8765). It is only a view, so it never alerts.
$dashboardUp = $false
try { $dashboardUp = (Invoke-WebRequest "http://127.0.0.1:8765/api/state" -UseBasicParsing -TimeoutSec 10).StatusCode -eq 200 } catch { }
if (-not $dashboardUp) {
    Note "Dashboard not answering; starting newsroom-dashboard."
    Start-ScheduledTask -TaskName "newsroom-dashboard" -ErrorAction SilentlyContinue
}
