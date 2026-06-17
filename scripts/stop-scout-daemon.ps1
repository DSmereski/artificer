$ErrorActionPreference = 'Continue'
$Needle = 'services.scout_daemon'

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

if (-not $procs) {
    Write-Host "scout-daemon not running."
    exit 0
}

foreach ($p in $procs) {
    Write-Host "Stopping scout-daemon (PID $($p.ProcessId))..."
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "scout-daemon stopped."
