# Register jf-dvr with the Windows Task Scheduler. Two tasks are created:
#
#   jf-dvr-bridge  The bridge itself. Runs as SYSTEM, starts at system boot
#                  (always available, even before anyone logs in).
#   jf-dvr-tray    The task-tray status icon. Runs in the logged-on user's
#                  interactive desktop session.
#
# Run from an elevated PowerShell:
#   powershell -NoProfile -File tools\install_service.ps1
#
# To remove:
#   Unregister-ScheduledTask -TaskName jf-dvr-bridge -Confirm:$false
#   Unregister-ScheduledTask -TaskName jf-dvr-tray   -Confirm:$false
#
# NOTE: ASCII-only on purpose. Windows PowerShell 5.1 reads a BOM-less .ps1
# as the system ANSI code page, which corrupts non-ASCII bytes.

$ErrorActionPreference = 'Stop'

# Project root = parent of this tools/ folder
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'

if (-not (Test-Path $python)) {
    Write-Error "venv not found: $python  (create .venv and install requirements first)"
    exit 1
}

# --- jf-dvr-bridge : the bridge process (SYSTEM, at startup) ------------------

# Launched via cmd so stdout/stderr can be appended to bridge.log
$bridgeAction = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c .venv\Scripts\python.exe run.py 1>> bridge.log 2>&1' -WorkingDirectory $root

$bridgeTrigger = New-ScheduledTaskTrigger -AtStartup

$bridgePrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

# No run-time limit; restart a few times if the process exits
$bridgeSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName 'jf-dvr-bridge' -Action $bridgeAction -Trigger $bridgeTrigger -Principal $bridgePrincipal -Settings $bridgeSettings -Force | Out-Null
Write-Output 'Registered scheduled task: jf-dvr-bridge'

# --- jf-dvr-tray : tray status icon (logged-on user, at logon) ---------------

# Current user, as DOMAIN\user. The tray icon appears in this user's session.
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (Test-Path $pythonw) {
    # pythonw.exe so no console window is shown
    $trayAction = New-ScheduledTaskAction -Execute $pythonw -Argument 'tools\tray.py' -WorkingDirectory $root

    $trayTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser

    # Interactive logon type: no password needed, runs in the desktop session
    $trayPrincipal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

    $traySettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew

    Register-ScheduledTask -TaskName 'jf-dvr-tray' -Action $trayAction -Trigger $trayTrigger -Principal $trayPrincipal -Settings $traySettings -Force | Out-Null
    Write-Output "Registered scheduled task: jf-dvr-tray (user: $currentUser)"
} else {
    Write-Warning "pythonw.exe not found, skipping tray task: $pythonw"
}

# --- start them and report ---------------------------------------------------

Start-ScheduledTask -TaskName 'jf-dvr-bridge'
try { Start-ScheduledTask -TaskName 'jf-dvr-tray' } catch { }
Start-Sleep -Seconds 10

Get-ScheduledTask -TaskName 'jf-dvr-*' | Select-Object TaskName, State | Format-Table -AutoSize
Get-ScheduledTaskInfo -TaskName 'jf-dvr-bridge' | Select-Object LastTaskResult, LastRunTime | Format-Table -AutoSize
