$ErrorActionPreference = 'Continue'
$Needle = 'vault_writer'

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }

if (-not $procs) {
    Write-Host "vault-writer not running."
    exit 0
}

foreach ($p in $procs) {
    Write-Host "Stopping vault-writer (PID $($p.ProcessId))..."
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Host "vault-writer stopped."
