# Start vault-writer daemon. Idempotent — exits early if already running.
# Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\scripts\start-vault-writer.ps1"

$ErrorActionPreference = 'Continue'

$Project = 'C:\Projects\Ai-Team'
$Python  = 'C:\Program Files\Python314\python.exe'
$LogDir  = 'C:\tmp\ai-team'
$Needle  = 'vault_writer'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

if ($existing) {
    Write-Host "vault-writer already running (PID $($existing.ProcessId))."
    exit 0
}

Write-Host "Starting vault-writer..."
Start-Process -FilePath $Python `
    -ArgumentList @('-u', '-m', 'vault_writer') `
    -WorkingDirectory $Project `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'vault-writer.log') `
    -RedirectStandardError  (Join-Path $LogDir 'vault-writer.log.err') | Out-Null

Write-Host "vault-writer started."
