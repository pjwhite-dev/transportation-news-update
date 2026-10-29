# One-time ELEVATED setup of the newsroom tasks. No password is stored or asked for:
# every task runs inside YOUR signed-in Windows session (the screen may be locked, but you
# must stay signed in). Safe to re-run; existing tasks are replaced.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows\install_tasks.ps1
#
# Replaces the earlier NSSM services and password-logon tasks, which stopped working when
# Windows rejected the stored password.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepoRoot = "",
    [string]$OllamaExe = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
    [string]$HermesExe = "$env:LOCALAPPDATA\hermes\bin\hermes.exe",
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"
# $PSScriptRoot is empty inside param() defaults in Windows PowerShell 5.1, so resolve it here.
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$user = "$env:USERDOMAIN\$env:USERNAME"
$pythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Missing $pythonw" }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
# conhost --headless runs PowerShell with no console window at all. "-WindowStyle Hidden" alone
# still flashes a black window for an instant on every launch, which is visible every 5 minutes.
$hidden = "conhost.exe"
$hiddenArgs = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass"

function New-NewsroomTask {
    param([string]$Name, [string]$Execute, [string]$Arguments, $Trigger, [switch]$LongRunning)
    $action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $RepoRoot
    $limit = if ($LongRunning) { [TimeSpan]::Zero } else { New-TimeSpan -Hours 3 }
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit $limit
    if ($PSCmdlet.ShouldProcess($Name, "Register scheduled task")) {
        Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger -Settings $settings `
            -Principal $principal -Force | Out-Null
        Write-Output "Registered $Name"
    }
}

function Every([int]$Minutes) {
    New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
}
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user

# 1. Retire the earlier NSSM services (they need the stored password), then bring the same
#    programs back as logon tasks so the PC is never without Ollama for long.
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
foreach ($service in "newsroom-ollama", "newsroom-hermes-gateway") {
    if ((Get-Service $service -ErrorAction SilentlyContinue) -and $PSCmdlet.ShouldProcess($service, "Remove NSSM service")) {
        if ($nssm) {
            $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
            & $nssm stop $service *> $null
            & $nssm remove $service confirm *> $null
            $ErrorActionPreference = $previous
        }
        else { sc.exe delete $service | Out-Null }
        Write-Output "Removed service $service"
    }
}

# 2. Services as hidden logon tasks (restarted by newsroom-services-check every 5 minutes).
$ollamaCommand = "`$env:OLLAMA_HOST='127.0.0.1'; `$env:OLLAMA_FLASH_ATTENTION='1'; `$env:OLLAMA_KV_CACHE_TYPE='q8_0'; " +
    "`$env:OLLAMA_MAX_LOADED_MODELS='2'; `$env:OLLAMA_NUM_PARALLEL='1'; & '$OllamaExe' serve"
New-NewsroomTask "newsroom-ollama" $hidden "$hiddenArgs -Command `"$ollamaCommand`"" $atLogon -LongRunning
New-NewsroomTask "newsroom-hermes-gateway" $hidden "$hiddenArgs -Command `"& '$HermesExe' gateway run`"" $atLogon -LongRunning
New-NewsroomTask "newsroom-wsl" $hidden "$hiddenArgs -Command `"wsl.exe -d $Distro -- sleep infinity`"" $atLogon -LongRunning
# The local newsroom dashboard (read-only view of the runs): http://127.0.0.1:8765
New-NewsroomTask "newsroom-dashboard" $pythonw "newsroom_dashboard.py" $atLogon -LongRunning

# 3. The scheduled jobs.
$weekdays430 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 4:30AM
New-NewsroomTask "newsroom-edition" $hidden "$hiddenArgs -File `"$RepoRoot\scripts\run_local_pipeline.ps1`"" $weekdays430
New-NewsroomTask "newsroom-ensure" $hidden "$hiddenArgs -File `"$RepoRoot\scripts\ensure_edition.ps1`"" (Every 15)
New-NewsroomTask "newsroom-services-check" $hidden "$hiddenArgs -File `"$RepoRoot\scripts\ensure_services.ps1`"" (Every 5)
New-NewsroomTask "newsroom-heartbeat" $pythonw "alerts.py ping pc-alive" (Every 5)
$weekdays815 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At 8:15AM
New-NewsroomTask "newsroom-doctor" $pythonw "doctor.py" $weekdays815
New-NewsroomTask "newsroom-tracker" $pythonw "-m tracker.core" (New-ScheduledTaskTrigger -Daily -At 4:00AM)

# 4. Start the logon tasks now so nothing waits for the next sign-in.
foreach ($name in "newsroom-ollama", "newsroom-hermes-gateway", "newsroom-wsl", "newsroom-dashboard") {
    if ($PSCmdlet.ShouldProcess($name, "Start now")) { Start-ScheduledTask -TaskName $name }
}

# 5. Never sleep; Windows Update active hours 02:00-20:00; turn on task history for diagnosis.
if ($PSCmdlet.ShouldProcess("power plan", "Never sleep on AC")) {
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
}
if ($PSCmdlet.ShouldProcess("Windows Update", "Set active hours 2-20")) {
    $ux = "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
    Set-ItemProperty $ux -Name ActiveHoursStart -Value 2 -Type DWord
    Set-ItemProperty $ux -Name ActiveHoursEnd -Value 20 -Type DWord
}
if ($PSCmdlet.ShouldProcess("Task Scheduler history", "Enable")) {
    wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true
}
Write-Output "Done. Check with: Get-ScheduledTask newsroom-* | Select TaskName,State"
