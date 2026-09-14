$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $repoRoot ".runtime"
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$n8n = "C:\Users\pjwhi\scoop\apps\nodejs-lts\current\bin\n8n.cmd"
if (-not (Test-Path -LiteralPath $n8n)) {
    throw "n8n is not installed at $n8n"
}

$env:N8N_HOST = "localhost"
$env:N8N_LISTEN_ADDRESS = "127.0.0.1"
$env:N8N_PORT = "5678"
$env:N8N_PROTOCOL = "http"
$env:N8N_SECURE_COOKIE = "false"
$env:N8N_RUNNERS_TASK_TIMEOUT = "900"
$env:N8N_CONCURRENCY_PRODUCTION_LIMIT = "1"
$env:N8N_UNVERIFIED_PACKAGES_ENABLED = "false"
$env:NODES_EXCLUDE = '["n8n-nodes-base.localFileTrigger"]'
$env:N8N_RESTRICT_FILE_ACCESS_TO = $repoRoot
$env:TRANSPORTATION_REPO = $repoRoot

& $n8n start
