#!/usr/bin/env bash
# Artificer install verifier — the P4 acceptance gate.
# Asserts a fresh setup actually works: config present, vault scaffolded,
# Ollama reachable + the model responds, and (if running) the gateway is up.
# Exit 0 = healthy.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
ok(){ echo "  ok  $1"; }
bad(){ echo "  !!  $1"; fail=1; }

[ -f "$ROOT/config/.env" ]            && ok "config/.env present"            || bad "config/.env missing — run the installer"
[ -d "$ROOT/vault" ]                  && ok "vault scaffolded"               || bad "vault/ missing"
[ -f "$ROOT/config/model_catalog.yaml" ] && ok "model catalog present"       || bad "model catalog missing"

if command -v ollama >/dev/null 2>&1; then
  ok "ollama installed"
  model="$(grep -m1 'ollama_name:' "$ROOT/config/model_catalog.yaml" | awk '{print $2}')"
  if [ -n "$model" ]; then
    if ollama run "$model" "reply with one word: ok" >/dev/null 2>&1; then
      ok "model responds ($model)"
    else
      bad "model '$model' did not respond — run: ollama pull $model"
    fi
  fi
else
  echo "  --  ollama not installed (cloud-only mode?) — skipping model check"
fi

# Gateway is optional at verify time (may not be started yet).
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/board/stats --max-time 4 2>/dev/null | grep -q 200; then
  ok "gateway up (http://127.0.0.1:8766)"
else
  echo "  --  gateway not running yet — start with: python -m gateway"
fi

if [ "$fail" -eq 0 ]; then echo "✅ Artificer install verified."; else echo "❌ issues above — see hints."; fi
exit "$fail"
