#!/usr/bin/env bash
# Artificer installer (Linux / macOS). Windows users: installer\install.ps1.
# Idempotent — re-run any time. Press Enter through for a working local setup.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
NI="${1:-}"
info(){ echo "[artificer] $1"; }
ask(){ if [ "$NI" = "--non-interactive" ]; then echo "$2"; else read -rp "$1 [$2]: " a; echo "${a:-$2}"; fi; }
have(){ command -v "$1" >/dev/null 2>&1; }

# 1. GPU detect → model tier
vram=0; gpu=none
if have nvidia-smi; then
  line=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
  gpu=$(echo "$line" | cut -d, -f1 | xargs); vram=$(echo "$line" | cut -d, -f2 | xargs)
fi
if [ "${vram:-0}" -ge 8000 ]; then rec=qwen2.5-coder:7b; else rec=qwen3:8b; fi
info "GPU: $gpu (${vram}MB). Recommended model: $rec"
model=$(ask "Local model to pull (or 'cloud')" "$rec")

# 2. Ollama
if [ "$model" != "cloud" ]; then
  if ! have ollama; then info "Install Ollama: https://ollama.com/download — then re-run."; exit 1; fi
  info "Pulling $model..."; ollama pull "$model"

  # Always pull the baseline helper + embedding models regardless of which
  # primary model was chosen. Required for vault search and helper roles.
  for bm in qwen2.5-coder:7b qwen3:8b gemma3:4b nomic-embed-text; do
    if [ "$bm" != "$model" ]; then
      info "Pulling baseline model $bm (skipped if already present)..."
      ollama pull "$bm"
    fi
  done
fi

# 3. Python deps
have python3 || { echo "Python 3 required"; exit 1; }
[ -f requirements.txt ] && { info "Installing deps..."; python3 -m pip install -q -r requirements.txt; }

# 4. Vault
vault=$(ask "Vault path" "./vault")
mkdir -p "$vault"/{canon,notes,plans}
[ -f "$vault/README.md" ] || echo "# Artificer vault" > "$vault/README.md"

# 5. Config
if [ ! -f config/.env ]; then
  cp config/.env.template config/.env
  cloud=$(ask "Anthropic API key (optional)" "")
  [ -n "$cloud" ] && echo "ANTHROPIC_API_KEY=$cloud" >> config/.env
  echo "HIVE_VAULT_PATH=$vault" >> config/.env
fi
theme=$(ask "Dashboard theme (warm-black/light/neutral-dark)" "warm-black")
echo "$theme" > config/.theme

info "Done. Start: python3 -m gateway | Dashboard: cd dashboard && npm ci && npm run build | Verify: bash installer/verify-install.sh"
