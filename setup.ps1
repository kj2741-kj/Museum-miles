# One-time setup for a fresh copy of this project on a new machine
# (2026-07-16, built for portability when this folder is shared with Mayank).
# Run once: right-click this file -> "Run with PowerShell", or run setup.bat.
#
# Does three things:
#   1. Checks Python is installed and on PATH (errors clearly if not).
#   2. Installs everything in requirements.txt.
#   3. Registers the three recurring Scheduled Tasks this project relies on,
#      using THIS machine's own Python path and THIS folder's own location --
#      never hardcoded, so the same script works identically wherever the
#      folder is copied to.

$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot

Write-Host "=== Museum Mile Funds -- Setup ===" -ForegroundColor Cyan
Write-Host "Project folder: $ProjectDir"

# --- 1. Find Python ---
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "ERROR: Python not found on PATH." -ForegroundColor Red
    Write-Host "Install Python 3.11+ from https://python.org (check 'Add python.exe to PATH' during install), then re-run this script."
    exit 1
}
$PythonExe = $pythonCmd.Source
Write-Host "Found Python: $PythonExe"

# --- 2. Install dependencies ---
Write-Host "Installing dependencies from requirements.txt..."
& $PythonExe -m pip install -r "$ProjectDir\requirements.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed -- see output above." -ForegroundColor Red
    exit 1
}

# --- 3. Register scheduled tasks (idempotent -- -Force overwrites if re-run) ---
Write-Host "Registering scheduled tasks..."

$currentUser = "$env:USERDOMAIN\$env:USERNAME"

# Orchestrator: auto-starts at login, manages port-25-gated SMTP vs discovery work
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "night_orchestrator.py" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "MuseumMile_NightOrchestrator_AutoStart" -Action $action -Trigger $trigger -Settings $settings -Description "Auto-starts night_orchestrator.py at login. See night_orchestrator.py." -Force | Out-Null
Write-Host "  - MuseumMile_NightOrchestrator_AutoStart (at login)"

# Weekly NFA firm-roster resync: catches new registrants + deregistrations
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "run_nfa_full_resync.py" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "MuseumMile_NFA_WeeklyResync" -Action $action -Trigger $trigger -Settings $settings -Description "Weekly full NFA CPO/CTA roster resync. See nfa_full_resync.py." -Force | Out-Null
Write-Host "  - MuseumMile_NFA_WeeklyResync (Sundays 3am)"

# Weekly SMTP recheck: resurfaces unverified emails for a fresh SMTP attempt
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "run_weekly_smtp_recheck.py" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 4am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "MuseumMile_Weekly_SMTP_Recheck" -Action $action -Trigger $trigger -Settings $settings -Description "Weekly reset of unverified emails for re-check. See run_weekly_smtp_recheck.py." -Force | Out-Null
Write-Host "  - MuseumMile_Weekly_SMTP_Recheck (Sundays 4am)"

# --- 4. Start the orchestrator right now too, not just at next login ---
# Ensure log dirs exist -- git doesn't track empty directories, and a fresh
# clone (as opposed to a straight folder copy) wouldn't have them yet.
foreach ($dir in @("logs\core", "logs\sec", "logs\cftc", "exports\sec", "exports\cftc", "data\sec")) {
    New-Item -ItemType Directory -Force -Path "$ProjectDir\$dir" | Out-Null
}

Write-Host "Starting night_orchestrator.py now..."
Start-Process -FilePath $PythonExe -ArgumentList "night_orchestrator.py" -WorkingDirectory $ProjectDir -WindowStyle Hidden `
    -RedirectStandardOutput "$ProjectDir\logs\core\night_orchestrator_stdout.log" `
    -RedirectStandardError "$ProjectDir\logs\core\night_orchestrator_stderr.log"

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Run start_dashboard.bat to open the dashboard."
Write-Host "The three scheduled tasks and the orchestrator are now running under this Windows account ($currentUser)."
