param(
    [string]$SupplementalFile = "",
    [switch]$SkipMetadataFetch,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing virtual environment at $python"
}

if (-not $env:AI_PROVIDER) { $env:AI_PROVIDER = "ollama" }
if (-not $env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434" }
if (-not $env:OLLAMA_MODEL) { $env:OLLAMA_MODEL = "qwen3.8:27b-q4_K_M" }
if (-not $env:OLLAMA_VERIFIER_MODEL) { $env:OLLAMA_VERIFIER_MODEL = "gemma4:31b" }
if (-not $env:OPENAI_FALLBACK_ENABLED) { $env:OPENAI_FALLBACK_ENABLED = "false" }

New-Item -ItemType Directory -Force -Path (Join-Path $repoRoot ".runtime") | Out-Null
try {
    # Never run two editions at once (newsroom-edition and newsroom-ensure can overlap).
    $editionLock = [IO.File]::Open((Join-Path $repoRoot ".runtime\edition.lock"), "OpenOrCreate", "ReadWrite", "None")
}
catch {
    Write-Output "Another edition run is active; exiting."
    return
}

# Keep a transcript of every run (scheduled tasks have no console); .runtime is git-ignored.
$logDir = Join-Path $repoRoot ".runtime\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
try { Start-Transcript -Path (Join-Path $logDir ("edition-" + (Get-Date -Format "yyyy-MM-dd-HHmmss") + ".log")) | Out-Null } catch { }

Push-Location $repoRoot
$supplementalPath = $null
# The local newsroom dashboard (newsroom_dashboard.py). Best effort: it can never fail the run.
function Send-Live {
    try { & $python newsroom_live.py @args 2>$null | Out-Null } catch { }
    $global:LASTEXITCODE = 0
}
$liveMode = if ($DryRun) { "rehearsal" } else { "edition" }
$liveLabel = "Edition " + (Get-Date -Format "yyyy-MM-dd")
if ($env:NEWSROOM_ATTEMPT) { $liveLabel += " (retry $($env:NEWSROOM_ATTEMPT))" }
Send-Live run-start $liveMode $liveLabel
$env:NEWSROOM_RUN_ACTIVE = "1"
if (-not $DryRun -and -not $env:NEWSROOM_ATTEMPT) {
    # The 04:30 run opens the dashboard in the browser (owner's request, 2026-10-29).
    try { Start-Process "http://127.0.0.1:8765/" } catch { }
}
try {
    if (-not $DryRun) {
        # The tracker ledger is regenerated from the Federal Register on every run (and by the
        # 04:00 tracker task) and the market snapshot is refetched, so uncommitted changes to just
        # these files are discarded.
        # Anything else dirty still stops the run.
        # The logic is in edition_preflight.py (unit-tested against a real repository, and editable by the
        # doctor). A failed earlier run today leaves its collected feed behind; that is discarded too.
        $preflight = & $python edition_preflight.py
        if ($LASTEXITCODE -ne 0) { throw ($preflight -join " ") }
        Write-Output $preflight
        git pull --ff-only origin main
        if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed." }
    }

    Send-Live event wire desk "Waking the writer and verifier models"
    & $python automated_briefing.py --health-check
    if ($LASTEXITCODE -ne 0) { throw "Ollama health check failed." }

    if (-not $DryRun) {
        Send-Live event wire desk "Gathering the news: Google News, the Federal Register, agency feeds and web search"
        & $python daily_update.py
        if ($LASTEXITCODE -ne 0) { throw "Public-source collection failed." }
    }

    $arguments = @("automated_briefing.py")
    if ($SupplementalFile) {
        if (-not (Test-Path -LiteralPath $SupplementalFile -PathType Leaf)) {
            throw "Supplemental email file is missing; refusing feed-only publication."
        }
        $supplementalPath = (Resolve-Path -LiteralPath $SupplementalFile).Path
        $arguments += @("--supplemental-file", $supplementalPath)
    }
    if ($DryRun) { $arguments += "--dry-run" }
    if ($SkipMetadataFetch) { $arguments += "--skip-metadata-fetch" }

    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Briefing generation or validation failed." }
    if ($DryRun) { Send-Live run-end ok "Rehearsal finished"; return }

    Send-Live event press desk "Final publication gate on the saved edition"
    & $python automated_briefing.py --validate-only data/latest_briefing.json --require-today
    if ($LASTEXITCODE -ne 0) { throw "Publication gate failed." }
    # Market watch is optional: market_watch.py never fails the run, and the site hides a stale box.
    & $python market_watch.py
    Send-Live event press desk "Printing the website"
    & $python public_site.py --output _site
    if ($LASTEXITCODE -ne 0) { throw "Static site build failed." }

    git add -- data/latest_raw_news.json data/raw_archive data/latest_briefing.json data/archive tracker/events.jsonl tracker/verifications.jsonl tracker/oira.json qa data/published_groups.json
    if (Test-Path tracker/discovered.yaml) { git add -- tracker/discovered.yaml }
    if (Test-Path data/market.json) { git add -- data/market.json }
    if ($LASTEXITCODE -ne 0) { throw "git add failed." }
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) { Write-Output "No publication changes."; Send-Live run-end ok "No publication changes"; return }
    if ($LASTEXITCODE -ne 1) { throw "Unable to inspect staged publication changes." }

    $editionDate = Get-Date
    $edition = $editionDate.ToString("yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
    $editionDisplay = $editionDate.ToString(
        "MMMM d, yyyy",
        [Globalization.CultureInfo]::GetCultureInfo("en-US")
    )
    Send-Live event delivery desk "Sending the edition to GitHub"
    git commit -m "Publish local Ollama news edition $edition"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed." }
    git push origin HEAD:main
    if ($LASTEXITCODE -ne 0) { throw "git push failed; the local commit is preserved for recovery." }

    $commitSha = (git rev-parse HEAD).Trim()
    Send-Live event delivery desk "Waiting for news.peterjwhite.org to show today's edition"
    $deploymentStatus = "pending"
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        Start-Sleep -Seconds 30
        try {
            # Exact marker only: free text such as "October 26, 2026" also appears on the previous
            # edition (docket dates), which once reported a false "verified".
            $response = Invoke-WebRequest -Uri ("https://news.peterjwhite.org/?cb=" + [guid]::NewGuid().ToString("N")) -TimeoutSec 20 -UseBasicParsing
            $hasEditionMarker = $response.Content -match ('<meta name="edition-id" content="' + [regex]::Escape($edition) + '">')
            if ($response.StatusCode -eq 200 -and $hasEditionMarker) {
                $deploymentStatus = "verified"
                break
            }
        }
        catch {
            $deploymentStatus = "waiting"
        }
    }
    if ($deploymentStatus -ne "verified") {
        throw "Git push succeeded at $commitSha, but the live deployment was not verified within five minutes."
    }
    & $python alerts.py ping edition-verified
    & $python alerts.py quiet "Edition $edition published and verified live."
    Send-Live run-end ok "Edition $edition published and verified live"
    [ordered]@{
        git_commit_sha = $commitSha
        deployment_status = $deploymentStatus
        live_url = "https://news.peterjwhite.org"
    } | ConvertTo-Json
}
catch {
    # No silent failures: every failed or incomplete run alerts the owner directly.
    # The 04:30 run and the last retry alert with a sound; retries 1-3 repeat the same news quietly.
    $attempt = 0
    if ($env:NEWSROOM_ATTEMPT) { $attempt = [int]$env:NEWSROOM_ATTEMPT }
    $failure = "Edition run FAILED: $($_.Exception.Message)"
    if ($attempt -ge 1) { $failure = "Retry $attempt of 4 failed: $($_.Exception.Message)" }
    if ($attempt -eq 4) { $failure += " (last automatic retry; the ensure task has stopped for today.)" }
    Send-Live run-end failed $failure
    if ($attempt -ge 1 -and $attempt -le 3) { & $python alerts.py quiet $failure } else { & $python alerts.py critical $failure }
    & $python alerts.py ping edition-verified --fail
    throw
}
finally {
    $runtimeRoot = Join-Path $repoRoot ".runtime"
    if (
        $supplementalPath -and
        $supplementalPath.StartsWith($runtimeRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $supplementalPath)
    ) {
        Remove-Item -LiteralPath $supplementalPath -Force
    }
    Pop-Location
    if ($editionLock) { $editionLock.Dispose() }
    try { Stop-Transcript | Out-Null } catch { }
}
