<#
.SYNOPSIS
    Registers the daily database backup as a Windows Scheduled Task (roadmap M15-T1).

.DESCRIPTION
    The production schedule is a Railway Cron job (`0 19 * * *` = 02:00 WIB,
    running `python -m app.jobs.backup`), which lands with M12-T4. This script is
    the equivalent for a machine that is not Railway — a laptop or a counter PC
    running the till locally, where the cron job does not exist.

    It creates a per-user task. No admin rights, nothing installed system-wide,
    and `-Remove` takes it away again.

    The task inherits whatever BACKUP_DIR is set in backend/.env. Point that at
    storage on a different machine or account before relying on it: a backup
    sitting next to the database is not a backup.

.PARAMETER At
    Time of day to run, 24-hour "HH:mm". Default 02:00 — after close, before open.

.PARAMETER TaskName
    Scheduled Task name. Default "WarungPintar-Backup".

.PARAMETER Remove
    Unregister the task instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\schedule-backup.ps1
    powershell -ExecutionPolicy Bypass -File scripts\schedule-backup.ps1 -At 03:30
    powershell -ExecutionPolicy Bypass -File scripts\schedule-backup.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$At = "02:00",
    [string]$TaskName = "WarungPintar-Backup",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

if (-not (Test-Path $python)) {
    throw "Backend virtualenv not found at $python. Create it first, then re-run this script."
}

# Fail now rather than at 02:00: the job needs the Postgres client tools.
$pgDump = (Get-Command pg_dump -ErrorAction SilentlyContinue).Source
if (-not $pgDump) {
    $local = Join-Path $repoRoot ".pg16\install\bin\pg_dump.exe"
    if (Test-Path $local) {
        $pgDump = $local
    } else {
        throw "pg_dump is not on PATH and $local does not exist. Install the Postgres 16 client tools or set PG_BIN_DIR in backend\.env."
    }
}

$action = New-ScheduledTaskAction -Execute $python -Argument "-m app.jobs.backup" -WorkingDirectory $backend
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Warung Pintar daily database backup (roadmap M15-T1)." -Force | Out-Null

Write-Host "Registered '$TaskName': $python -m app.jobs.backup, daily at $At."
Write-Host "  working directory : $backend"
Write-Host "  pg_dump           : $pgDump"
Write-Host ""
Write-Host "Run it once now to check it works:"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Then read the run log:"
Write-Host "  Get-Content <BACKUP_DIR>\backup-log.jsonl -Tail 1"
