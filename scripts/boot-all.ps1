# Ai-Team full-stack boot - PowerShell (robust).
#
# Replaces boot-all.cmd, which relied on wmic.exe (removed by default on
# Windows 11 24H2 / build 26200) and on cmd-batch quirks (CRLF, paren
# escaping). This launcher:
#   1. Starts Ollama (start-all.ps1 does not cover it).
#   2. Delegates vault-writer + gateway + Terry + scout-daemon to
#      start-all.ps1, which is idempotent (skips anything already alive
#      via Get-CimInstance, not wmic).
#
# Invoked by the "Ai-Team Boot" scheduled task at logon. Safe to re-run.

$ErrorActionPreference = 'Continue'

$Project = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$LogDir  = if ($env:HIVE_LOG_DIR) { $env:HIVE_LOG_DIR } else { Join-Path $env:TEMP 'ai-team' }
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

Write-Host "===== Ai-Team Boot (PowerShell) ====="

# 1. Ollama - LLM backend; everything else needs it.
$oll = Get-Process ollama -ErrorAction SilentlyContinue
if ($oll) {
    Write-Host "[1] Ollama already running (PID $($oll[0].Id))."
} else {
    Write-Host "[1] Starting Ollama (tuned)..."
    Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', "`"$PSScriptRoot\start-ollama-tuned.cmd`"" `
        -WorkingDirectory $Project -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $c.Connect('127.0.0.1', 11434)
            if ($c.Connected) { $c.Close(); break }
        } catch { Start-Sleep -Milliseconds 700 }
    }
    Write-Host "[1] Ollama started."
}

# 2-5. vault-writer, gateway, Terry, scout-daemon (idempotent).
Write-Host "[2] Delegating to start-all.ps1..."
& "$PSScriptRoot\start-all.ps1"

Write-Host "===== Ai-Team Boot complete ====="
