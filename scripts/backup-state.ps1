# backup-state.ps1 - Online-safe backup of all Ai-Team persistent state.
#
# WHAT IT BACKS UP
#   1. vault.db  (knowledge graph, crew board, token ledger, lessons, pairings)
#      Path: <vault_path>\.vault-writer\vault.db
#      Method: Python sqlite3.Connection.backup() - the stdlib online-backup API.
#              Safe while vault-writer AND gateway hold the DB open (WAL mode).
#   2. gateway state_dir DBs: calendar.db, hive_nodes.db, hive_jobs.db
#      Method: Python sqlite3.Connection.backup() for each .db file.
#   3. gateway state_dir flat files: devices.json, *.jsonl, scout-history.jsonl
#      Method: plain file copy (no lock concerns).
#
# DEST:   C:\Backups\ai-team\YYYY-MM-DD\   (configurable via $BackupRoot)
# RETAIN: last 7 dated directories (older pruned on success).
#
# USAGE
#   # Manual run:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Projects\Ai-Team\scripts\backup-state.ps1"
#
#   # With custom root:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\backup-state.ps1" -BackupRoot D:\Backups\ai-team
#
#   # Scheduled via install-backup-task.ps1 (daily 04:00).
#
# EXIT CODES
#   0 = success (all targets backed up)
#   1 = one or more targets failed

[CmdletBinding()]
param(
    [string]$BackupRoot = 'C:\Backups\ai-team',
    [int]$RetainDays    = 7
)

$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------- paths

$VaultDb   = if ($env:HIVE_VAULT_PATH) { Join-Path $env:HIVE_VAULT_PATH '.vault-writer\vault.db' } else { '.\vault\.vault-writer\vault.db' }
$StateDir  = if ($env:HIVE_GATEWAY_STATE_DIR) { $env:HIVE_GATEWAY_STATE_DIR } else { Join-Path $env:USERPROFILE '.ai-team-gateway' }
$Python    = if ($env:HIVE_PYTHON) { $env:HIVE_PYTHON } else { 'python' }

# ---------------------------------------------------------------- dest

$DateTag  = (Get-Date -Format 'yyyy-MM-dd')
$DestDir  = Join-Path $BackupRoot $DateTag
$null = New-Item -ItemType Directory -Path $DestDir -Force

# ---------------------------------------------------------------- helpers

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$ts] $Msg"
}

function Format-Bytes {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return '{0:0.00} GB' -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return '{0:0.0} MB'  -f ($Bytes / 1MB) }
    return '{0} KB' -f [math]::Ceiling($Bytes / 1KB)
}

$TotalBytes = 0
$Errors     = 0

# ---------------------------------------------------------------- sqlite online backup (Python stdlib)

function Backup-SqliteDb {
    param(
        [string]$Src,
        [string]$DestFile
    )
    if (-not (Test-Path -LiteralPath $Src)) {
        Write-Log "SKIP  $Src  (not found)"
        return
    }
    # Use Python stdlib sqlite3.Connection.backup - the only portable
    # online-backup path that works while another process holds the DB open.
    $PyCode = @"
import sqlite3, sys
src_path, dst_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path, timeout=10)
dst = sqlite3.connect(dst_path)
try:
    src.backup(dst, pages=100)
finally:
    dst.close()
    src.close()
"@
    $TempScript = Join-Path $env:TEMP 'ai_team_backup_sqlite.py'
    [System.IO.File]::WriteAllText($TempScript, $PyCode, [System.Text.UTF8Encoding]::new($false))
    $Result = & $Python $TempScript $Src $DestFile 2>&1
    Remove-Item -LiteralPath $TempScript -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR backing up $Src : $Result"
        $script:Errors++
        return
    }
    $Size = (Get-Item -LiteralPath $DestFile -ErrorAction SilentlyContinue).Length
    $script:TotalBytes += $Size
    Write-Log "OK    $(Split-Path $Src -Leaf) -> $DestFile  ($(Format-Bytes $Size))"
}

# ---------------------------------------------------------------- flat-file copy

function Backup-File {
    param(
        [string]$Src,
        [string]$DestFile
    )
    if (-not (Test-Path -LiteralPath $Src)) {
        return  # silently skip optional files
    }
    Copy-Item -LiteralPath $Src -Destination $DestFile -Force
    $Size = (Get-Item -LiteralPath $DestFile -ErrorAction SilentlyContinue).Length
    $script:TotalBytes += $Size
    Write-Log "OK    $(Split-Path $Src -Leaf) -> $DestFile  ($(Format-Bytes $Size))"
}

# ---------------------------------------------------------------- run backups

Write-Log "===== Ai-Team backup starting ====="
Write-Log "Destination: $DestDir"

# 1. vault.db
Backup-SqliteDb -Src $VaultDb -DestFile (Join-Path $DestDir 'vault.db')

# 2. gateway state_dir SQLite databases
foreach ($DbName in @('calendar.db', 'hive_nodes.db', 'hive_jobs.db')) {
    $Src = Join-Path $StateDir $DbName
    Backup-SqliteDb -Src $Src -DestFile (Join-Path $DestDir $DbName)
}

# 3. gateway state_dir flat files (devices.json, *.jsonl)
$FlatFiles = @('devices.json', 'scout-history.jsonl', 'recent-images.jsonl', 'asset_imports.json')
foreach ($FName in $FlatFiles) {
    Backup-File -Src (Join-Path $StateDir $FName) -DestFile (Join-Path $DestDir $FName)
}
# Also pick up any other .jsonl files in state_dir root
Get-ChildItem -LiteralPath $StateDir -Filter '*.jsonl' -ErrorAction SilentlyContinue |
    Where-Object { $FlatFiles -notcontains $_.Name } |
    ForEach-Object {
        Backup-File -Src $_.FullName -DestFile (Join-Path $DestDir $_.Name)
    }

# ---------------------------------------------------------------- prune old backups

$Pruned = 0
$Dirs = Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
    Sort-Object Name -Descending
if ($Dirs.Count -gt $RetainDays) {
    $ToRemove = $Dirs | Select-Object -Skip $RetainDays
    foreach ($Dir in $ToRemove) {
        Remove-Item -LiteralPath $Dir.FullName -Recurse -Force -ErrorAction SilentlyContinue
        Write-Log "PRUNED $($Dir.Name)"
        $Pruned++
    }
}

# ---------------------------------------------------------------- summary

Write-Log "===== Backup complete ====="
Write-Log "Total backed up : $(Format-Bytes $TotalBytes)"
Write-Log "Directories kept: $([math]::Min($Dirs.Count + 1, $RetainDays))  (pruned $Pruned old)"
if ($Errors -gt 0) {
    Write-Log "ERRORS: $Errors target(s) failed - check log above"
    exit 1
}
Write-Log "All targets backed up successfully."
exit 0
