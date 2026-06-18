# Install / uninstall the "Ai-Team Boot" Windows scheduled task.
#
# Why Task Scheduler instead of shell:startup or a Windows service:
#   - Need a real user session for CUDA/Discord/Ollama → "At log on of <user>"
#   - Need /MIN windows that survive reboot → only Task Scheduler delivers
#   - Need restart-on-fail semantics → service-like via task settings
#   - shell:startup runs after login but doesn't survive UAC re-prompts
#
# The task name is `Ai-Team Boot`. Re-running this script overwrites it.
# Pass -Uninstall to remove the task.
#
# Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File install-autostart.ps1
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File install-autostart.ps1 -Uninstall

[CmdletBinding()]
param (
    [switch]$Uninstall,
    [string]$TaskName = "Ai-Team Boot"
)

$ErrorActionPreference = 'Stop'

$Project    = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$BootScript = Join-Path $PSScriptRoot 'boot-all.ps1'

if ($Uninstall) {
    if (schtasks /Query /TN $TaskName 2>$null) {
        schtasks /Delete /TN $TaskName /F | Out-Null
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task '$TaskName' found — nothing to remove."
    }
    return
}

if (-not (Test-Path -LiteralPath $BootScript)) {
    throw "boot-all.ps1 not found at $BootScript"
}

# Build an XML definition for full control over conditions/restart-on-fail.
# Inline XML is easier than schtasks /XML round-tripping with parameters.
$XmlPath = Join-Path $env:TEMP 'ai-team-boot.xml'
$User = "$env:USERDOMAIN\$env:USERNAME"
$XmlBody = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>$User</Author>
    <Description>Launches the full Ai-Team stack at user logon: Ollama, vault-writer, scout-daemon, gateway, Terry.</Description>
    <URI>\$TaskName</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$User</UserId>
      <Delay>PT15S</Delay>
    </LogonTrigger>
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
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -File "$BootScript"</Arguments>
      <WorkingDirectory>$Project</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# Write as UTF-16 LE with BOM (schtasks /XML requires it on Windows 10+).
[System.IO.File]::WriteAllText(
    $XmlPath, $XmlBody, [System.Text.UnicodeEncoding]::new($false, $true)
)

# Delete + recreate to make this idempotent.
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

Write-Host ""
Write-Host "===== Ai-Team autostart installed ====="
Write-Host "  Task name : $TaskName"
Write-Host "  Trigger   : at logon of $User (15-second delay)"
Write-Host "  Action    : $BootScript"
Write-Host "  Restart   : up to 3x at 1-minute intervals on failure"
Write-Host ""
Write-Host "Verify with:"
Write-Host "  schtasks /Query /TN `"$TaskName`" /V /FO LIST"
Write-Host ""
Write-Host "Run it now without rebooting:"
Write-Host "  schtasks /Run /TN `"$TaskName`""
Write-Host ""
Write-Host "Uninstall later:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Uninstall"
