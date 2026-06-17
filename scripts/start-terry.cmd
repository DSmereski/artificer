@echo off
set "PROJECT=C:\Projects\Ai-Team"
set "PYTHON=C:\Program Files\Python314\python.exe"
set "LOGDIR=C:\tmp\ai-team"
mkdir "%LOGDIR%" 2>nul
echo Starting Terry...
start "Terry" /MIN cmd /c "cd /d "%PROJECT%" && set CUDA_VISIBLE_DEVICES=1,2 && "%PYTHON%" -u bots/terry/bot.py >> "%LOGDIR%\terry.log" 2>&1"
echo Terry started.
