<#
  Hiveforge installer (Windows-first).

  One guided setup: detects your GPU/VRAM, recommends a model tier, installs +
  verifies Ollama and the model, installs Python deps, scaffolds the vault,
  writes config from templates, lets you pick a model / theme / optional cloud
  key, and starts the gateway.

  Idempotent + resumable — re-run any time. Every prompt has a sane default;
  press Enter through the whole thing for a working local setup.

  Usage:  powershell -ExecutionPolicy Bypass -File installer\install.ps1
          ... -NonInteractive   # accept all defaults, no prompts
#>
[CmdletBinding()]
param([switch]$NonInteractive)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Info($m){ Write-Host "[hiveforge] $m" -ForegroundColor Cyan }
function Ok($m){   Write-Host "  ok  $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  !!  $m" -ForegroundColor Yellow }
function Ask($q,$def){
  if ($NonInteractive) { return $def }
  $a = Read-Host "$q [$def]"
  if ([string]::IsNullOrWhiteSpace($a)) { return $def } else { return $a }
}
function Have($exe){ [bool](Get-Command $exe -ErrorAction SilentlyContinue) }

Info "Hiveforge setup — press Enter to accept each [default]."

# ── 1. Detect GPU / VRAM → recommend a model tier ──────────────────────────
$vram = 0; $gpu = "none"
if (Have nvidia-smi) {
  try {
    $line = (& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($line) { $parts = $line -split ','; $gpu = $parts[0].Trim(); $vram = [int]($parts[1].Trim()) }
  } catch {}
}
if ($vram -ge 16000)   { $recModel = 'qwen2.5-coder:7b'; $tier = "GPU ${vram}MB — comfortable" }
elseif ($vram -ge 8000){ $recModel = 'qwen2.5-coder:7b'; $tier = "GPU ${vram}MB — good" }
elseif ($vram -gt 0)   { $recModel = 'qwen3:8b';         $tier = "GPU ${vram}MB — small" }
else                   { $recModel = 'qwen3:8b';         $tier = "no GPU detected — CPU or cloud" }
Info "GPU: $gpu ($tier). Recommended local model: $recModel"
$model = Ask "Local model to pull (or 'cloud' to skip Ollama)" $recModel

# ── 2. Ollama ──────────────────────────────────────────────────────────────
if ($model -ne 'cloud') {
  if (-not (Have ollama)) {
    Warn "Ollama not found. Install it from https://ollama.com/download then re-run."
    if (-not $NonInteractive) { Start-Process "https://ollama.com/download" }
    throw "Ollama required for local models (or re-run with model=cloud)."
  }
  Ok "Ollama present"
  Info "Pulling $model (skipped if already present)..."
  & ollama pull $model
  Ok "model ready: $model"
}

# ── 3. Python deps ─────────────────────────────────────────────────────────
if (-not (Have python)) { throw "Python 3 required — install from https://python.org" }
if (Test-Path "$Root\requirements.txt") {
  Info "Installing Python deps..."
  & python -m pip install -q -r "$Root\requirements.txt"
  Ok "deps installed"
}

# ── 4. Vault scaffold ──────────────────────────────────────────────────────
$vault = Ask "Vault path (Obsidian-compatible notes dir)" ".\vault"
if (-not (Test-Path $vault)) { New-Item -ItemType Directory -Force -Path $vault | Out-Null }
foreach ($d in 'canon','notes','plans') { New-Item -ItemType Directory -Force -Path (Join-Path $vault $d) | Out-Null }
if (-not (Test-Path (Join-Path $vault 'README.md'))) {
  "# Hiveforge vault`n`nOpen this folder in Obsidian (optional)." | Set-Content -Encoding utf8 (Join-Path $vault 'README.md')
}
Ok "vault: $vault"

# ── 5. Config from templates + optional cloud key / theme ──────────────────
$envFile = "$Root\config\.env"
if (-not (Test-Path $envFile)) {
  Copy-Item "$Root\config\.env.template" $envFile
  $cloud = Ask "Anthropic API key (optional, Enter to skip)" ""
  if ($cloud) { Add-Content $envFile "`nANTHROPIC_API_KEY=$cloud" }
  Add-Content $envFile "`nHIVE_VAULT_PATH=$vault"
  Ok "wrote config/.env"
} else { Ok "config/.env exists (left as-is)" }

$theme = Ask "Dashboard theme (warm-black / light / neutral-dark)" "warm-black"
Set-Content -Encoding utf8 "$Root\config\.theme" $theme
Ok "theme: $theme"

# ── 6. Start ───────────────────────────────────────────────────────────────
Info "Setup complete."
Write-Host ""
Write-Host "  Start the gateway:   python -m gateway" -ForegroundColor White
Write-Host "  Build the dashboard: cd dashboard && npm ci && npm run build" -ForegroundColor White
Write-Host "  Verify:              bash installer/verify-install.sh" -ForegroundColor White
Write-Host ""
Ok "Hiveforge is ready. Re-run this installer any time — it's idempotent."
