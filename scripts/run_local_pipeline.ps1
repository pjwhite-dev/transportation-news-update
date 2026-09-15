param(
    [string]$SupplementalFile = "",
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
if (-not $env:OLLAMA_MODEL) { $env:OLLAMA_MODEL = "qwen3.6:27b-q4_K_M" }
if (-not $env:OPENAI_FALLBACK_ENABLED) { $env:OPENAI_FALLBACK_ENABLED = "false" }

Push-Location $repoRoot
$supplementalPath = $null
try {
    if (-not $DryRun) {
        $dirty = git status --porcelain
        if ($dirty) { throw "Refusing production run because the repository is not clean." }
        git pull --ff-only origin main
        if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed." }
    }

    & $python automated_briefing.py --health-check
    if ($LASTEXITCODE -ne 0) { throw "Ollama health check failed." }

    if (-not $DryRun) {
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

    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Briefing generation or validation failed." }
    if ($DryRun) { return }

    & $python automated_briefing.py --validate-only data/latest_briefing.json
    if ($LASTEXITCODE -ne 0) { throw "Publication gate failed." }
    & $python public_site.py --output _site
    if ($LASTEXITCODE -ne 0) { throw "Static site build failed." }

    git add -- data/latest_raw_news.json data/raw_archive data/latest_briefing.json data/archive
    if ($LASTEXITCODE -ne 0) { throw "git add failed." }
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) { Write-Output "No publication changes."; return }
    if ($LASTEXITCODE -ne 1) { throw "Unable to inspect staged publication changes." }

    $editionDate = Get-Date
    $edition = $editionDate.ToString("yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
    $editionDisplay = $editionDate.ToString(
        "MMMM d, yyyy",
        [Globalization.CultureInfo]::GetCultureInfo("en-US")
    )
    git commit -m "Publish local Ollama news edition $edition"
    if ($LASTEXITCODE -ne 0) { throw "git commit failed." }
    git push origin HEAD:main
    if ($LASTEXITCODE -ne 0) { throw "git push failed; the local commit is preserved for recovery." }

    $commitSha = (git rev-parse HEAD).Trim()
    $deploymentStatus = "pending"
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        Start-Sleep -Seconds 30
        try {
            $response = Invoke-WebRequest -Uri "https://news.peterjwhite.org" -TimeoutSec 20 -UseBasicParsing
            $hasEditionMarker =
                $response.Content -match [regex]::Escape($edition) -or
                $response.Content -match [regex]::Escape($editionDisplay)
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
    [ordered]@{
        git_commit_sha = $commitSha
        deployment_status = $deploymentStatus
        live_url = "https://news.peterjwhite.org"
    } | ConvertTo-Json
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
}
