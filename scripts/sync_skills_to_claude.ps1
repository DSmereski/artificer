# M3: Bridge the vault `skills/` folder into Claude Code's skills tree.
#
# Tries a junction (works without admin on Windows). Falls back to
# a directory copy if the link cannot be created.
#
# Usage:
#   powershell.exe -ExecutionPolicy Bypass -File "...\scripts\sync_skills_to_claude.ps1"

$ErrorActionPreference = 'Continue'

$VaultSkills  = if ($env:HIVE_VAULT_PATH) { Join-Path $env:HIVE_VAULT_PATH 'skills' } else { Join-Path $env:USERPROFILE 'Ai-Team-Vault\skills' }
$ClaudeSkills = "$env:USERPROFILE\.claude\skills"
$Target       = Join-Path $ClaudeSkills 'team'

if (-not (Test-Path $VaultSkills)) {
    Write-Host "vault skills dir not found at $VaultSkills"
    exit 1
}

if (-not (Test-Path $ClaudeSkills)) {
    New-Item -ItemType Directory -Force -Path $ClaudeSkills | Out-Null
}

# If the target already exists and is a junction/link to the right
# place, we're done.
$existing = Get-Item $Target -ErrorAction SilentlyContinue
if ($existing) {
    $isLink = $existing.Attributes -match 'ReparsePoint'
    if ($isLink) {
        $resolved = (Get-Item $Target).Target
        if ($resolved -and ($resolved -ieq $VaultSkills)) {
            Write-Host "skills bridge OK: $Target -> $VaultSkills"
            exit 0
        }
        Write-Host "removing stale link: $Target"
        Remove-Item $Target -Force
    } else {
        Write-Host "removing existing dir (will replace): $Target"
        Remove-Item $Target -Recurse -Force
    }
}

# Junction first (no admin needed).
try {
    New-Item -ItemType Junction -Path $Target -Target $VaultSkills | Out-Null
    Write-Host "created junction $Target -> $VaultSkills"
    exit 0
} catch {
    Write-Host "junction failed: $_"
}

# Symlink fallback (requires Developer Mode or admin).
try {
    New-Item -ItemType SymbolicLink -Path $Target -Target $VaultSkills | Out-Null
    Write-Host "created symlink $Target -> $VaultSkills"
    exit 0
} catch {
    Write-Host "symlink failed: $_"
}

# Last-ditch: directory copy. Edits in either place won't propagate
# until this script is re-run, so the junction path is preferred.
Write-Host "falling back to copy"
Copy-Item -Path "$VaultSkills\*" -Destination $Target -Recurse -Force
Write-Host "copied skills to $Target (re-run to sync)"
