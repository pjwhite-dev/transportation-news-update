# Runs every 15 minutes (task newsroom-ensure). Starts today's edition late if it is
# missing, never runs two editions at once, caps retries, and alerts at 8:00 a.m. ET.
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$runtime = Join-Path $repoRoot ".runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

$eastern = [TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
$now = [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $eastern)
$today = $now.ToString("yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)

if ($now.DayOfWeek -in @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)) { return }
if ($now.TimeOfDay -lt [TimeSpan]::FromHours(4.5)) { return }

Push-Location $repoRoot
try {
    $state = (& $python scripts/check_publication_state.py --mode any).Trim()
    if ($state -eq "SKIP") { return }

    # 8:00 a.m. ET deadline: alert once per day, then keep trying.
    $alertMarker = Join-Path $runtime "late-alert-$today.marker"
    if ($now.TimeOfDay -ge [TimeSpan]::FromHours(8) -and -not (Test-Path $alertMarker)) {
        & $python alerts.py critical "No verified edition for $today as of 8:00 a.m. ET. The ensure task is retrying."
        New-Item -ItemType File -Path $alertMarker -Force | Out-Null
    }

    $attemptsFile = Join-Path $runtime "attempts-$today.txt"
    $attempts = 0
    if (Test-Path $attemptsFile) { $attempts = [int](Get-Content $attemptsFile) }
    if ($attempts -ge 4) { return }   # the failed runs already alerted; stop hammering

    # Skip quietly while an edition run holds the lock (run_local_pipeline.ps1 takes it).
    try {
        $probe = [IO.File]::Open((Join-Path $runtime "edition.lock"), "OpenOrCreate", "ReadWrite", "None")
        $probe.Dispose()
    }
    catch { return }

    Set-Content -Path $attemptsFile -Value ($attempts + 1)
    if ($DryRun) { Write-Output "Would start edition run $($attempts + 1) for $today."; return }
    $env:NEWSROOM_ATTEMPT = [string]($attempts + 1)
    & (Join-Path $PSScriptRoot "run_local_pipeline.ps1")
}
finally {
    Pop-Location
}
