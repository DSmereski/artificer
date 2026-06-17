# restart-gateway.ps1 - Drain-then-restart the ai-team gateway.
#
# THIS IS THE CORRECT WAY to pick up gateway-side changes without
# orphaning crash-restarts. Use this after pulling commits that touch:
#   - QA swimlane, /v1/stt, GPU defaults, board auth, dispatcher logic,
#     any gateway Python module.
#
# ----------------------------------------------------------------
# SIGNAL APPROACH (and its tradeoff)
# ----------------------------------------------------------------
# Windows limitation: the gateway is launched with -WindowStyle Hidden
# (no console), so there is no console handle to send CTRL_BREAK to,
# and Python on Windows does not register a SIGTERM handler unless
# explicitly called. Stop-Process -Force is therefore the only reliable
# kill on Windows for a hidden-window process.
#
# TRADEOFF: Stop-Process -Force is an immediate TerminateProcess(). The
# FastAPI lifespan `finally` block (dispatcher.stop, store.close) does
# NOT run. This means:
#   - Any in-progress hive turn is hard-killed (safe: the task's
#     heartbeat expires after STALE_INPROGRESS_S=600s and the reaper
#     bounces it back to ready on the next start).
#   - SQLite WAL is left in a consistent state (WAL mode + checkpoint
#     on next open) - no data loss.
#   - The board pause flag is persisted to the DB, so the dispatcher
#     will not pick up new tasks until we POST /board/resume below.
#
# MITIGATION: we POST /board/pause FIRST so the dispatcher drains
# (stops claiming new tasks). We then wait for in_progress == 0 before
# killing, so in-flight claude-code turns finish cleanly. The hard kill
# only fires once the board is idle (or after the 120s timeout if a
# task is genuinely stuck).
#
# ALTERNATIVE: if a future version runs the gateway under a proper
# Windows service wrapper (NSSM / WinSW) with CTRL_BREAK, replace
# stop-gateway.ps1 with a graceful CTRL_BREAK and this script will
# automatically benefit.
# ----------------------------------------------------------------
#
# USAGE
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\restart-gateway.ps1"
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\restart-gateway.ps1" -Token "your-device-bearer-token"
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\restart-gateway.ps1" -SkipPause
#
# PARAMETERS
#   -Token      Bearer token for a paired device. Required for /board/pause
#               and /board/resume. If omitted the script attempts to read one
#               from the devices store (first enabled device); if none is
#               found, it falls back to graceful-drain-only (no pause/resume
#               HTTP calls, relies on SIGTERM drain).
#   -SkipPause  Skip the pause/drain step entirely - use only for development
#               when no tasks are running.
#   -DrainTimeout  Seconds to wait for in_progress to reach 0 (default 120).

[CmdletBinding()]
param(
    [string]$Token        = '',
    [switch]$SkipPause,
    [int]$DrainTimeout    = 120
)

$ErrorActionPreference = 'Continue'

# ----------------------------------------------------------------
# Concurrency lock - prevents the RESTART-RACE WEDGE: two restarts run
# back-to-back, the 2nd's stop-gateway kills the 1st mid-startup, and the
# gateway is left half-initialised (port listening, every request hangs).
# Hit this 3x on 2026-06-16. A second restart now ABORTS while one is live.
# ----------------------------------------------------------------
$LockFile = Join-Path $env:TEMP 'ai-team-gateway-restart.lock'
if (Test-Path -LiteralPath $LockFile) {
    $lockAge = ((Get-Date) - (Get-Item -LiteralPath $LockFile).LastWriteTime).TotalSeconds
    if ($lockAge -lt 300) {
        Write-Host "[restart] another restart is in progress (lock $([int]$lockAge)s old). Aborting to avoid the restart-race wedge. Re-run after it finishes."
        exit 2
    }
    Write-Host "[restart] stale restart lock ($([int]$lockAge)s) - overriding."
}
Set-Content -LiteralPath $LockFile -Value $PID -Force
trap { Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue; break }

$Project         = if ($env:HIVE_PROJECT_ROOT) { $env:HIVE_PROJECT_ROOT } else { Split-Path $PSScriptRoot -Parent }
$GatewayBase     = 'http://127.0.0.1:8766'
$DevicesJson     = if ($env:HIVE_GATEWAY_STATE_DIR) { Join-Path $env:HIVE_GATEWAY_STATE_DIR 'devices.json' } else { Join-Path $env:USERPROFILE '.ai-team-gateway\devices.json' }
$StopScript      = Join-Path $Project 'scripts\stop-gateway.ps1'
$StartScript     = Join-Path $Project 'scripts\start-gateway.ps1'

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$ts] $Msg"
}

# ----------------------------------------------------------------
# Resolve bearer token: param > devices.json > none
# ----------------------------------------------------------------

function Get-DeviceToken {
    if (-not (Test-Path -LiteralPath $DevicesJson)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $DevicesJson -Raw -ErrorAction Stop
        $data = $raw | ConvertFrom-Json -ErrorAction Stop
        # devices.json is a dict keyed by device id; find first with a token field
        $props = $data.PSObject.Properties | Select-Object -First 10
        foreach ($p in $props) {
            $dev = $p.Value
            $tok = $dev.token
            if ($tok -and $tok.Length -gt 10) {
                return $tok
            }
        }
    } catch {
        # Swallow - we'll proceed without a token
    }
    return $null
}

$BearerToken = $Token
if (-not $BearerToken) {
    $BearerToken = Get-DeviceToken
    if ($BearerToken) {
        Write-Log "Using device token from $DevicesJson"
    } else {
        Write-Log "No bearer token available - pause/resume HTTP calls will be skipped"
    }
}

# ----------------------------------------------------------------
# Helper: invoke a board endpoint with auth
# ----------------------------------------------------------------

function Invoke-Board {
    param(
        [string]$Method,
        [string]$Path,
        [string]$BToken
    )
    $uri = "$GatewayBase$Path"
    try {
        $headers = @{}
        if ($BToken) {
            $headers['Authorization'] = "Bearer $BToken"
        }
        $resp = Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -TimeoutSec 10 -ErrorAction Stop
        return $resp
    } catch {
        Write-Log "HTTP $Method $Path failed: $_"
        return $null
    }
}

function Get-BoardState {
    try {
        return Invoke-RestMethod -Method GET -Uri "$GatewayBase/board/state" -TimeoutSec 10 -ErrorAction Stop
    } catch {
        return $null
    }
}

# ----------------------------------------------------------------
# Step 1: Pause the board
# ----------------------------------------------------------------

if (-not $SkipPause) {
    # pause/resume are loopback-exempt (board.py _require_board_admin), so the
    # local restart needs no token. Bearer is still sent if we found one.
    Write-Log "Pausing board dispatcher..."
    $r = Invoke-Board -Method POST -Path '/board/pause' -BToken $BearerToken
    if ($r -ne $null) {
        Write-Log "Board paused: $($r.paused)"
    } else {
        Write-Log "Pause HTTP call failed - gateway may be down already; proceeding"
    }
}

# ----------------------------------------------------------------
# Step 2: Wait for in_progress to drain (bounded)
# ----------------------------------------------------------------

if (-not $SkipPause) {
    Write-Log "Waiting for in_progress == 0 (timeout ${DrainTimeout}s)..."
    $deadline = [System.DateTime]::UtcNow.AddSeconds($DrainTimeout)
    $drained  = $false
    while ([System.DateTime]::UtcNow -lt $deadline) {
        $state = Get-BoardState
        if ($state -eq $null) {
            Write-Log "Gateway not responding - treating as drained"
            $drained = $true
            break
        }
        $inProgress = 0
        if ($state.PSObject.Properties['in_progress']) {
            $inProgress = [int]$state.in_progress
        } elseif ($state.PSObject.Properties['by_status']) {
            $bs = $state.by_status.in_progress
            if ($null -eq $bs) { $bs = 0 }
            $inProgress = [int]$bs
        }
        if ($inProgress -eq 0) {
            Write-Log "Board drained (in_progress = 0)"
            $drained = $true
            break
        }
        Write-Log "  in_progress = $inProgress, waiting..."
        Start-Sleep -Seconds 5
    }
    if (-not $drained) {
        Write-Log "WARNING: drain timeout (${DrainTimeout}s) reached with tasks still in_progress."
        Write-Log "  Worktree isolation makes a killed hive build safely reapable after restart."
        Write-Log "  A live claude-code turn may lose progress (will be re-attempted)."
        Write-Log "  Proceeding with restart..."
    }
}

# ----------------------------------------------------------------
# Step 3: Stop the gateway
# ----------------------------------------------------------------

Write-Log "Stopping gateway..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StopScript
Start-Sleep -Seconds 3

# Confirm it's gone
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape('-m gateway') }
if ($procs) {
    Write-Log "Gateway still running after stop script - force-killing PID(s): $($procs.ProcessId -join ', ')"
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}
Write-Log "Gateway stopped."

# ----------------------------------------------------------------
# Step 4: Start the gateway
# ----------------------------------------------------------------

Write-Log "Starting gateway..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript

# ----------------------------------------------------------------
# Step 5: Wait for gateway health, then resume board
# ----------------------------------------------------------------

Write-Log "Waiting for gateway to become healthy..."
# No /health route exists; /board/state is the readiness probe (returns the
# board JSON once app.state.crew_store is wired). Any 200 JSON == healthy.
$healthDeadline = [System.DateTime]::UtcNow.AddSeconds(60)
$healthy = $false
while ([System.DateTime]::UtcNow -lt $healthDeadline) {
    try {
        $h = Invoke-RestMethod -Method GET -Uri "$GatewayBase/board/state" -TimeoutSec 5 -ErrorAction Stop
        if ($null -ne $h) {
            $healthy = $true
            break
        }
    } catch {
        # Not up yet
    }
    Start-Sleep -Seconds 3
}

if (-not $healthy) {
    Write-Log "WARNING: gateway did not respond to /health within 60s - may need more time."
} else {
    Write-Log "Gateway is healthy."
}

# Resume the board regardless: if the gateway was paused before (persisted
# flag) we must clear it, otherwise the dispatcher stays idle after restart.
if (-not $SkipPause) {
    Write-Log "Resuming board dispatcher..."
    # Small extra wait to let app.state finish wiring
    Start-Sleep -Seconds 2
    $r = Invoke-Board -Method POST -Path '/board/resume' -BToken $BearerToken
    if ($r -ne $null) {
        $resumeState = if ($r.paused -eq $false) { 'active' } else { 'still paused?' }
        Write-Log "Board resumed: $resumeState"
    } else {
        Write-Log "WARNING: /board/resume failed. Run manually:"
        Write-Log "  Invoke-RestMethod -Method POST -Uri '$GatewayBase/board/resume' -Headers @{Authorization='Bearer <token>'}"
    }
}

Write-Log "===== Gateway restart complete ====="
Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
