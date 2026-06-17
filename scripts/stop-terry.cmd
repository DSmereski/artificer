@echo off
for /f "tokens=2" %%a in ('wmic process where "CommandLine like '%%terry%%bot%%' and Name='python.exe'" get ProcessId 2^>nul ^| findstr /r "[0-9]"') do (
    echo Stopping Terry (PID %%a)...
    taskkill /PID %%a /F >nul 2>&1
)
echo Terry stopped.
