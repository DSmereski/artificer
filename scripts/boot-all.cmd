@echo off
REM ================================================================
REM  Ai-Team boot launcher — full stack in dependency order.
REM
REM  Order:
REM    1. Ollama        (LLM backend; everything else needs it)
REM    2. vault-writer  (TCP daemon at 127.0.0.1:8765)
REM    3. scout-daemon  (watchdog + RPC at 127.0.0.1:8767)
REM    4. gateway       (FastAPI on 127.0.0.1:8766 / Tailscale)
REM    5. Terry         (Discord bot — talks to gateway + Ollama)
REM
REM  Idempotent: safe to re-run; skips anything already alive.
REM  Run by Task Scheduler at user logon — see install-autostart.ps1
REM  for the schtasks setup.
REM ================================================================

setlocal EnableDelayedExpansion

REM Repo root: use HIVE_PROJECT_ROOT if set, else auto-detect from this
REM script's own location (scripts\boot-all.cmd -> parent dir).
if defined HIVE_PROJECT_ROOT (
    set "PROJECT=%HIVE_PROJECT_ROOT%"
) else (
    set "PROJECT=%~dp0.."
)
set "PYTHON=C:\Program Files\Python314\python.exe"
set "LOGDIR=C:\tmp\ai-team"

mkdir "%LOGDIR%" 2>nul

echo [%date% %time%] ===== Ai-Team Boot All =====

REM ----------------------------------------------------------------
REM  1. OLLAMA — LLM backend (must be first; gateway + Terry call it)
REM ----------------------------------------------------------------

echo [1/5] Checking Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | findstr /I "ollama.exe" > nul
if !ERRORLEVEL! EQU 0 (
    echo       Ollama is already running.
) else (
    echo       Starting Ollama ^(tuned: NUM_PARALLEL=1, KEEP_ALIVE=24h, GPUs 1+2^)...
    call "%PROJECT%\scripts\start-ollama-tuned.cmd"
    REM Give Ollama ~10s to bind :11434 before gateway probes it.
    timeout /t 10 /nobreak > nul
    echo       Ollama started.
)

REM ----------------------------------------------------------------
REM  2. VAULT-WRITER — vault learn/upsert daemon
REM ----------------------------------------------------------------

echo [2/5] Checking vault-writer...
wmic process where "CommandLine like '%%vault_writer%%' and Name='python.exe'" get ProcessId 2>nul | findstr /r "[0-9]" > nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo       vault-writer is already running.
) else (
    echo       Starting vault-writer...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\scripts\start-vault-writer.ps1"
    timeout /t 3 /nobreak > nul
    echo       vault-writer started.
)

REM ----------------------------------------------------------------
REM  3. SCOUT DAEMON — watchdog, GPU/disk monitor, sysmon RPC
REM ----------------------------------------------------------------

echo [3/5] Checking scout-daemon...
wmic process where "CommandLine like '%%services.scout_daemon%%' and Name='python.exe'" get ProcessId 2>nul | findstr /r "[0-9]" > nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo       scout-daemon is already running.
) else (
    echo       Starting scout-daemon...
    start "scout-daemon" /MIN cmd /c "cd /d "%PROJECT%" && "%PYTHON%" -u -m services.scout_daemon >> "%LOGDIR%\scout-daemon.log" 2>&1"
    timeout /t 3 /nobreak > nul
    echo       scout-daemon started.
)

REM ----------------------------------------------------------------
REM  4. GATEWAY — FastAPI hub + hive coordinator
REM ----------------------------------------------------------------

echo [4/5] Checking gateway...
wmic process where "CommandLine like '%%-m gateway%%' and Name='python.exe'" get ProcessId 2>nul | findstr /r "[0-9]" > nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo       Gateway is already running.
) else (
    echo       Starting gateway...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\scripts\start-gateway.ps1"
    timeout /t 5 /nobreak > nul
    echo       Gateway started.
)

REM ----------------------------------------------------------------
REM  5. TERRY — Discord bot (last; needs gateway + Ollama up)
REM ----------------------------------------------------------------

echo [5/5] Checking Terry...
wmic process where "CommandLine like '%%terry%%bot%%' and Name='python.exe'" get ProcessId 2>nul | findstr /r "[0-9]" > nul 2>&1
if !ERRORLEVEL! EQU 0 (
    echo       Terry is already running.
) else (
    echo       Starting Terry ^(GPUs 1,2^)...
    start "Terry" /MIN cmd /c "cd /d "%PROJECT%" && set CUDA_VISIBLE_DEVICES=1,2 && "%PYTHON%" -u bots/terry/bot.py >> "%LOGDIR%\terry.log" 2>&1"
    timeout /t 10 /nobreak > nul
    echo       Terry started.
)

echo.
echo ===== Ai-Team boot complete =====
echo   Ollama       : LLM backend (port 11434, GPUs 1+2)
echo   vault-writer : vault daemon (127.0.0.1:8765)
echo   scout-daemon : watchdog + monitor (127.0.0.1:8767)
echo   Gateway      : FastAPI hub (127.0.0.1:8766 + Tailscale)
echo   Terry        : Discord bot (GPUs 1,2)
echo.
echo Logs: %LOGDIR%\
echo.
endlocal
