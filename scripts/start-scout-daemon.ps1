# Start the scout-daemon (headless watchdog + monitor + RPC).
# Idempotent. Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\scripts\start-scout-daemon.ps1"

$ErrorActionPreference = 'Continue'

$Project = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$Python  = if ($env:HIVE_PYTHON) { $env:HIVE_PYTHON } else { (Get-Command python -ErrorAction SilentlyContinue)?.Source ?? 'python' }
$LogDir  = if ($env:HIVE_LOG_DIR) { $env:HIVE_LOG_DIR } else { Join-Path $env:TEMP 'ai-team' }
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
