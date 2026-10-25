# Registers the newsroom scheduled tasks under the current Windows account (plan 7.9).
# Run once from an elevated PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\windows\install_tasks.ps1
# Windows prompts for the account password so the tasks run whether or not you are signed in.
# Safe to re-run: existing tasks are replaced. Pass -WhatIf to preview.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$credential = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message "Windows password for the newsroom tasks"

function New-NewsroomTask {
    param([string]$Name, [string]$Execute, [string]$Arguments, $Trigger)
    $action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $RepoRoot
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -WakeToRun `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 3)
    if ($PSCmdlet.ShouldProcess($Name, "Register scheduled task")) {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings `
            -User $credential.UserName -Password $credential.GetNetworkCredential().Password `
            -RunLevel Limited -Force | Out-Null
        Write-Output "Registered $Name"
    }
}

$every15 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
$every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 5)
$weekdays430 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 4:30AM

$pwsh = "powershell.exe"
New-NewsroomTask "newsroom-edition" $pwsh "-NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\scripts\run_local_pipeline.ps1`"" $weekdays430
New-NewsroomTask "newsroom-ensure" $pwsh "-NoProfile -ExecutionPolicy Bypass -File `"$RepoRoot\scripts\ensure_edition.ps1`"" $every15
New-NewsroomTask "newsroom-heartbeat" $python "alerts.py ping pc-alive" $every5
New-NewsroomTask "newsroom-tracker" $python "-m tracker.core" (New-ScheduledTaskTrigger -Daily -At 4:00AM)
