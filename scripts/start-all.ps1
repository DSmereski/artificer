# Ai-Team Bot Launcher — vault-writer + gateway + Terry + scout-daemon.
# Idempotent: skips any process already running.
# Usage (from any shell):
#   powershell.exe -NoProfile -File "C:\Projects\Ai-Team\scripts\start-all.ps1"

$ErrorActionPreference = 'Continue'

$Project = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$Python  = if ($env:HIVE_PYTHON) { $env:HIVE_PYTHON } else { 'python' }
$LogDir  = if ($env:HIVE_LOG_DIR) { $env:HIVE_LOG_DIR } else { Join-Path $env:TEMP 'ai-team' }

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Test-BotRunning {
    param([string]$Needle)
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -match [regex]::Escape($Needle)) {
            return $p.ProcessId
        }
    }
    return $null
}

function Start-Bot {
    param(
        [string]$Name,
        [string]$Script,      # path relative to $Project, e.g. 'bots\maggy\bot.py'
        [string]$Needle,      # substring in commandline used to detect already-running
        [string]$LogFile,
        [hashtable]$Env = @{} # extra env vars (e.g. CUDA_VISIBLE_DEVICES)
    )

    $existing = Test-BotRunning -Needle $Needle
    if ($existing) {
        Write-Host "      $Name already running (PID $existing)."
        return
    }

    Write-Host "      Starting $Name..."

    # Apply env vars in this process — they inherit to the child.
    $saved = @{}
    foreach ($k in $Env.Keys) {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
        [Environment]::SetEnvironmentVariable($k, $Env[$k], 'Process')
    }

    try {
        $fullScript = Join-Path $Project $Script
        # -WindowStyle Hidden detaches without a console window.
        # Quote the script path explicitly — Start-Process doesn't quote array
        # args containing spaces, and the path may contain spaces.
        # Without the quotes Python may misparse the script arg.
        Start-Process -FilePath $Python `
            -ArgumentList @('-u', "`"$fullScript`"") `
            -WorkingDirectory $Project `
            -WindowStyle Hidden `
            -RedirectStandardOutput $LogFile `
            -RedirectStandardError  "$LogFile.err" | Out-Null
    }
    finally {
        foreach ($k in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process')
        }
    }

    Write-Host "      $Name started."
}

Write-Host "===== Ai-Team Start All ====="

Write-Host "[0/3] Checking vault-writer..."
$vwExisting = Test-BotRunning -Needle 'vault_writer'
if ($vwExisting) {
    Write-Host "      vault-writer already running (PID $vwExisting)."
} else {
    Write-Host "      Starting vault-writer..."
    Start-Process -FilePath $Python `
        -ArgumentList @('-u', '-m', 'vault_writer') `
        -WorkingDirectory $Project `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'vault-writer.log') `
        -RedirectStandardError  (Join-Path $LogDir 'vault-writer.log.err') | Out-Null
    # Wait up to 15s for the daemon to start listening on 127.0.0.1:8765.
    $deadline = (Get-Date).AddSeconds(15)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', 8765)
            if ($c.Connected) { $c.Close(); $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if ($ready) {
        Write-Host "      vault-writer ready."
    } else {
        Write-Host "      WARN: vault-writer did not open its socket within 15s. Bots will start anyway."
    }
}

Write-Host "[0.5/3] Checking ai-team-gateway..."
$gwExisting = Test-BotRunning -Needle '-m gateway'
if ($gwExisting) {
    Write-Host "      ai-team-gateway already running (PID $gwExisting)."
} else {
    Write-Host "      Starting ai-team-gateway..."
    Start-Process -FilePath $Python `
        -ArgumentList @('-u', '-m', 'gateway') `
        -WorkingDirectory $Project `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'gateway.log') `
        -RedirectStandardError  (Join-Path $LogDir 'gateway.log.err') | Out-Null
    # Wait up to 10s for the gateway to open its port (default 8766).
    $deadline = (Get-Date).AddSeconds(10)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', 8766)
            if ($c.Connected) { $c.Close(); $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if ($ready) {
        Write-Host "      ai-team-gateway ready."
    } else {
        Write-Host "      WARN: ai-team-gateway did not open its socket within 10s."
    }
}

Write-Host "[1/2] Checking Terry..."
Start-Bot -Name 'Terry' `
    -Script 'bots\terry\bot.py' `
    -Needle 'bots\terry\bot.py' `
    -LogFile (Join-Path $LogDir 'terry.log') `
    -Env @{ CUDA_VISIBLE_DEVICES = '1,2' }

Write-Host "[2/2] Checking scout-daemon..."
$scoutExisting = Test-BotRunning -Needle 'services.scout_daemon'
if ($scoutExisting) {
    Write-Host "      scout-daemon already running (PID $scoutExisting)."
} else {
    Write-Host "      Starting scout-daemon..."
    Start-Process -FilePath $Python `
        -ArgumentList @('-u', '-m', 'services.scout_daemon') `
        -WorkingDirectory $Project `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'scout.log') `
        -RedirectStandardError  (Join-Path $LogDir 'scout.log.err') | Out-Null
    Write-Host "      scout-daemon started."
}

Write-Host ""
Write-Host "===== All started ====="
Write-Host "  vault-writer : TCP daemon (127.0.0.1:8765)"
Write-Host "  gateway      : FastAPI (127.0.0.1:8766)"
Write-Host "  Terry        : Voice/Text/Image bot (GPUs 1,2)"
Write-Host "  scout-daemon : Watchdog + RPC (127.0.0.1:8767)"
Write-Host ""
Write-Host "Logs: $LogDir\"
