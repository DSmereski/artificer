# Start the scout-daemon (headless watchdog + monitor + RPC).
# Idempotent. Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\scripts\start-scout-daemon.ps1"

$ErrorActionPreference = 'Continue'

$Project = 'C:\Projects\Ai-Team'
$Python  = 'C:\Program Files\Python314\python.exe'
$LogDir  = 'C:\tmp\ai-team'
$Needle  = 'services.scout_daemon'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

if ($existing) {
    Write-Host "scout-daemon already running (PID $($existing.ProcessId))."
    exit 0
}

Write-Host "Starting scout-daemon..."
Start-Process -FilePath $Python `
    -ArgumentList @('-u', '-m', 'services.scout_daemon') `
    -WorkingDirectory $Project `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'scout-daemon.log') `
    -RedirectStandardError  (Join-Path $LogDir 'scout-daemon.log.err') | Out-Null

Write-Host "scout-daemon started."
