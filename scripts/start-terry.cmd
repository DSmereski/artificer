@echo off
REM Repo root: use HIVE_PROJECT_ROOT if set, else auto-detect from this
REM script's own location (scripts\start-terry.cmd -> parent dir).
if defined HIVE_PROJECT_ROOT (
    set "PROJECT=%HIVE_PROJECT_ROOT%"
) else (
    set "PROJECT=%~dp0.."
)
set "PYTHON=C:\Program Files\Python314\python.exe"
set "LOGDIR=C:\tmp\ai-team"
mkdir "%LOGDIR%" 2>nul
echo Starting Terry...
start "Terry" /MIN cmd /c "cd /d "%PROJECT%" && set CUDA_VISIBLE_DEVICES=1,2 && "%PYTHON%" -u bots/terry/bot.py >> "%LOGDIR%\terry.log" 2>&1"
echo Terry started.
