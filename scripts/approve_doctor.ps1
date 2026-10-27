# One-step approval of a doctor-prepared fix:  scripts\approve_doctor.ps1 -Branch doctor/2026-10-27
# Shows what changed, asks for a yes, fast-forwards main to the branch, pushes (never forced),
# then runs the edition (it resumes from today's saved draft when one exists) and reports the result.
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
Push-Location $repoRoot
try {
    if ($Branch -notmatch '^doctor/\d{4}-\d{2}-\d{2}$') { throw "Only doctor/<date> branches can be approved here." }
    git rev-parse --verify --quiet "refs/heads/$Branch" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Branch $Branch does not exist." }
    if ((git branch --show-current).Trim() -ne "main") { throw "Check out main in $repoRoot first." }
    git fetch origin main
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed." }
    git merge-base --is-ancestor HEAD $Branch
    if ($LASTEXITCODE -ne 0) { throw "$Branch is not based on the current main; ask for a fresh doctor run instead." }

    Write-Output "Files the doctor changed:"
    git diff --stat "HEAD..$Branch"
    if (-not $Yes) {
        $answer = Read-Host "Merge $Branch into main, push, and rerun today's edition? (yes/no)"
        if ($answer -ne "yes") { Write-Output "Cancelled; nothing changed."; return }
    }

    git merge --ff-only $Branch
    if ($LASTEXITCODE -ne 0) { throw "Fast-forward merge failed; nothing was pushed." }
    git push origin main
    if ($LASTEXITCODE -ne 0) { throw "git push failed; the merge is local only." }
    & $python alerts.py quiet "Approved $Branch and merged to main. Rerunning the edition."
    & (Join-Path $PSScriptRoot "run_local_pipeline.ps1")
}
finally {
    Pop-Location
}
