# One-time, ELEVATED setup so the stack survives reboots (rebuild plan 7.9).
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_services.ps1
# Windows prompts once for your account password (services run under YOUR account, no new user).
# Safe to re-run. Use -WhatIf to preview. Needs NSSM:  winget install NSSM.NSSM
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$OllamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    [string]$HermesExe = "$env:LOCALAPPDATA\hermes\bin\hermes.exe",
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated (Administrator) PowerShell."
}
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) { throw "NSSM not found. Install it with: winget install NSSM.NSSM  (then reopen PowerShell)." }
$credential = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for the newsroom services"
$password = $credential.GetNetworkCredential().Password

function Install-NssmService {
    param([string]$Name, [string]$Exe, [string]$Arguments, [string[]]$Environment)
    if (-not $PSCmdlet.ShouldProcess($Name, "Install/refresh NSSM service")) { return }
    & $nssm stop $Name 2>$null | Out-Null
    & $nssm remove $Name confirm 2>$null | Out-Null
    & $nssm install $Name $Exe $Arguments | Out-Null
    & $nssm set $Name ObjectName $credential.UserName $password | Out-Null
    & $nssm set $Name Start SERVICE_AUTO_START | Out-Null
    & $nssm set $Name AppExit Default Restart | Out-Null
    & $nssm set $Name AppRestartDelay 5000 | Out-Null
    & $nssm set $Name AppStdout "$env:LOCALAPPDATA\newsroom-logs\$Name.log" | Out-Null
    & $nssm set $Name AppStderr "$env:LOCALAPPDATA\newsroom-logs\$Name.err.log" | Out-Null
    & $nssm set $Name AppRotateFiles 1 | Out-Null
    if ($Environment) { & $nssm set $Name AppEnvironmentExtra @Environment | Out-Null }
    & $nssm start $Name | Out-Null
    Write-Output "Installed and started $Name"
}

New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\newsroom-logs" | Out-Null

# 1. Ollama as a boot service (plan 7.5 settings). The tray app's auto-start is disabled
#    so two servers never fight over port 11434.
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
foreach ($name in "Ollama", "ollama app") {
    if ((Get-ItemProperty $run -ErrorAction SilentlyContinue).$name) {
        if ($PSCmdlet.ShouldProcess($name, "Remove tray auto-start")) { Remove-ItemProperty $run -Name $name }
    }
}
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" -Filter "Ollama*" -ErrorAction SilentlyContinue |
    ForEach-Object { if ($PSCmdlet.ShouldProcess($_.Name, "Remove tray auto-start")) { Remove-Item $_.FullName } }
Get-Process "ollama app", "ollama" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($PSCmdlet.ShouldProcess("PID $($_.Id)", "Stop existing Ollama so the service owns the port")) { Stop-Process $_ -Force }
}
Install-NssmService "newsroom-ollama" $OllamaExe "serve" @(
    "OLLAMA_HOST=127.0.0.1", "OLLAMA_FLASH_ATTENTION=1", "OLLAMA_KV_CACHE_TYPE=q8_0",
    "OLLAMA_MAX_LOADED_MODELS=2", "OLLAMA_NUM_PARALLEL=1"
)

# 2. Hermes gateway as a boot service (its default Windows login item only starts at logon).
Install-NssmService "newsroom-hermes-gateway" $HermesExe "gateway run" $null
$loginItem = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Hermes_Gateway.vbs"
if (Test-Path $loginItem) {
    if ($PSCmdlet.ShouldProcess($loginItem, "Remove duplicate logon starter")) { Remove-Item $loginItem }
}

# 3. WSL Ubuntu (SearXNG) at boot: a task that keeps the distro running.
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d $Distro -- sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries
if ($PSCmdlet.ShouldProcess("newsroom-wsl", "Register boot task")) {
    Register-ScheduledTask -TaskName "newsroom-wsl" -Action $action -Trigger $trigger -Settings $settings `
        -User $credential.UserName -Password $password -RunLevel Limited -Force | Out-Null
    Write-Output "Registered newsroom-wsl"
}
Write-Output "Also run once inside WSL so SearXNG survives idle:  wsl -d $Distro -- sudo loginctl enable-linger `$USER"

# 4. Power and update policy: never sleep; Windows Update active hours 02:00-20:00.
if ($PSCmdlet.ShouldProcess("power plan", "Never sleep on AC")) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /change disk-timeout-ac 0
}
if ($PSCmdlet.ShouldProcess("Windows Update", "Set active hours 2-20")) {
    $ux = "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
    Set-ItemProperty $ux -Name ActiveHoursStart -Value 2 -Type DWord
    Set-ItemProperty $ux -Name ActiveHoursEnd -Value 20 -Type DWord
}
Write-Output "Done. Verify with:  sc query newsroom-ollama ; sc query newsroom-hermes-gateway ; schtasks /Query /TN newsroom-wsl"
