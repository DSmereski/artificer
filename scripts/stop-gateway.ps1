$ErrorActionPreference = 'Continue'
$Needle = '-m gateway'

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

if (-not $procs) {
    Write-Host "ai-team-gateway not running."
    exit 0
}

foreach ($p in $procs) {
    Write-Host "Stopping ai-team-gateway (PID $($p.ProcessId))..."
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "ai-team-gateway stopped."
