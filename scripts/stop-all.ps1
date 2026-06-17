# Ai-Team Stopper — kills Terry, scout-daemon, gateway, vault-writer.
# Usage (from any shell):
#   powershell.exe -NoProfile -File "C:\Projects\Ai-Team\scripts\stop-all.ps1"

$ErrorActionPreference = 'Continue'

function Stop-Bot {
    param([string]$Name, [string]$Needle)

    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

    if (-not $procs) {
        Write-Host "$Name not running."
        return
    }

    foreach ($p in $procs) {
        Write-Host "Stopping $Name (PID $($p.ProcessId))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Stopping all..."
Stop-Bot -Name 'Terry'        -Needle 'bots\terry\bot.py'
Stop-Bot -Name 'scout-daemon' -Needle 'services.scout_daemon'
Stop-Bot -Name 'gateway'      -Needle '-m gateway'
Stop-Bot -Name 'vault-writer' -Needle 'vault_writer'
Write-Host "All stopped."
