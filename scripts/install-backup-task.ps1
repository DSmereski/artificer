# install-backup-task.ps1 — Register a daily Task Scheduler job for backup-state.ps1.
#
# Registers the "Ai-Team Backup" task:
#   Trigger : daily at 04:00 AM
#   Action  : backup-state.ps1 (online-safe SQLite + flat-file backup)
#   User    : current interactive user (needs a logged-in session for paths)
#
# Must be run as Administrator (schtasks /Create for another user requires elevation).
#
# USAGE
#   # Install (run as Admin):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install-backup-task.ps1"
#
#   # Uninstall:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\install-backup-task.ps1" -Uninstall
#
#   # Manual one-off run (no elevation needed):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\backup-state.ps1"

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$TaskName   = 'Ai-Team Backup',
    [string]$BackupRoot = 'C:\Backups\ai-team'
)

$ErrorActionPreference = 'Stop'

$Project      = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$BackupScript = Join-Path $PSScriptRoot 'backup-state.ps1'

if ($Uninstall) {
    schtasks /Query /TN $TaskName 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        schtasks /Delete /TN $TaskName /F | Out-Null
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task '$TaskName' found — nothing to remove."
    }
    return
}

if (-not (Test-Path -LiteralPath $BackupScript)) {
    throw "backup-state.ps1 not found at $BackupScript"
}

$XmlPath = Join-Path $env:TEMP 'ai-team-backup.xml'
$User    = "$env:USERDOMAIN\$env:USERNAME"
$Args    = "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`" -BackupRoot `"$BackupRoot`""

$XmlBody = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$User</Author>
    <Description>Daily online-safe backup of Ai-Team vault.db, gateway state DBs, and flat files to $BackupRoot\YYYY-MM-DD\. Retains last 7 dated directories.</Description>
    <URI>\$TaskName</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2024-01-01T04:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$User</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT15M</Interval>
      <Count>2</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$Args</Arguments>
      <WorkingDirectory>$Project</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

[System.IO.File]::WriteAllText(
    $XmlPath, $XmlBody, [System.Text.UnicodeEncoding]::new($false, $true)
)

schtasks /Query /TN $TaskName 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    schtasks /Delete /TN $TaskName /F | Out-Null
    Write-Host "Removed existing task '$TaskName' before re-creating."
}

schtasks /Create /TN $TaskName /XML $XmlPath
if ($LASTEXITCODE -ne 0) {
    throw "schtasks /Create failed (exit $LASTEXITCODE)."
}

Remove-Item -LiteralPath $XmlPath -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '===== Ai-Team Backup task installed ====='
Write-Host "  Task name : $TaskName"
Write-Host '  Trigger   : daily at 04:00 AM'
Write-Host "  Action    : $BackupScript"
Write-Host "  Dest root : $BackupRoot\YYYY-MM-DD\"
Write-Host '  Retain    : last 7 dated directories'
Write-Host ''
Write-Host 'Verify with:'
Write-Host "  schtasks /Query /TN `"$TaskName`" /V /FO LIST"
Write-Host ''
Write-Host 'Run it now without waiting for 4am:'
Write-Host "  schtasks /Run /TN `"$TaskName`""
Write-Host ''
Write-Host 'Or run the script directly (no elevation):'
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$BackupScript`""
Write-Host ''
Write-Host 'Uninstall later:'
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Uninstall"
