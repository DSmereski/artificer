# Start ai-team-gateway. Idempotent.
# Usage:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\scripts\start-gateway.ps1"

$ErrorActionPreference = 'Continue'

$Project = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$Python  = if ($env:HIVE_PYTHON) { $env:HIVE_PYTHON } else { (Get-Command python -ErrorAction SilentlyContinue)?.Source ?? 'python' }
$LogDir  = if ($env:HIVE_LOG_DIR) { $env:HIVE_LOG_DIR } else { Join-Path $env:TEMP 'ai-team' }
$Needle  = '-m gateway'

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

if ($existing) {
    # A process exists - but is it actually SERVING, or a hung zombie? Probe
    # the loopback health endpoint. If it responds, we're up (skip). If it
    # does NOT respond (hung at startup / deadlocked), kill it and start fresh
    # - otherwise a zombie permanently blocks recovery (observed 2026-06-14).
    $healthy = $false
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8766/board/state' -TimeoutSec 5 -ErrorAction Stop | Out-Null
        $healthy = $true
    } catch { $healthy = $false }

    if ($healthy) {
        Write-Host "ai-team-gateway already running + healthy (PID $($existing.ProcessId))."
        exit 0
    }

    Write-Host "ai-team-gateway process exists (PID $($existing.ProcessId)) but is NOT responding - killing hung process."
    foreach ($p in $existing) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
    }
    Start-Sleep -Seconds 2
}

Write-Host "Starting ai-team-gateway..."

# Optional secrets file - gitignored - overrides env vars on launch.
# Format: KEY=value, one per line, # comments OK.
$SecretsFile = Join-Path $PSScriptRoot '.env.local'
if (Test-Path $SecretsFile) {
    Get-Content $SecretsFile | ForEach-Object {
        if ($_ -match '^\s*#') { return }
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $name = $matches[1]; $value = $matches[2].Trim('"').Trim("'")
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

Start-Process -FilePath $Python `
    -ArgumentList @('-u', '-m', 'gateway') `
    -WorkingDirectory $Project `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'gateway.log') `
    -RedirectStandardError  (Join-Path $LogDir 'gateway.log.err') | Out-Null

Write-Host "ai-team-gateway started."
