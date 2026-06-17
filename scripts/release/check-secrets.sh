#!/usr/bin/env bash
# Gate: NO live secrets in the public tree OR its git history.
# Exit 0 = clean, 1 = findings. CI-required (P1/P6).
#
# Prefers gitleaks (scans full history — the REAL surface; a sanitized export
# scan is blind to secrets in old commits / gitignored .env). Falls back to a
# pattern + tracked-file scan when gitleaks is absent.
#
# Usage: scripts/release/check-secrets.sh [target_dir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
TARGET="${1:-.}"
echo "== check-secrets == ($TARGET)"
fail=0

if command -v gitleaks >/dev/null 2>&1; then
  if [ -d "$TARGET/.git" ]; then
    echo "  gitleaks: scanning full git history..."
    gitleaks detect --source "$TARGET" --no-banner --redact || fail=1
  else
    echo "  gitleaks: scanning files (no .git — export mode)..."
    gitleaks detect --source "$TARGET" --no-git --no-banner --redact || fail=1
  fi
else
  echo "  gitleaks NOT installed — fallback pattern scan (install gitleaks for full-history coverage)"
  # 1. No tracked real secret files (only *.example / *.template allowed).
  if [ -d "$TARGET/.git" ]; then
    tracked_secrets="$(git -C "$TARGET" ls-files | grep -iE '(^|/)\.env$|\.env\.local$|\.env\.[a-z]+$|secrets?\.(ya?ml|json)$|\.cookie$|devices\.json$' | grep -ivE '\.example$|\.template$' || true)"
    if [ -n "$tracked_secrets" ]; then
      echo "  [FAIL] tracked secret-bearing files:"; echo "$tracked_secrets" | sed 's/^/      /'; fail=1
    fi
    # 2. The known live-secret files must have ZERO history.
    for f in config/.env scripts/.env.local .env; do
      n="$(git -C "$TARGET" log --all --oneline -- "$f" 2>/dev/null | wc -l | tr -d ' ')"
      [ "$n" != "0" ] && { echo "  [FAIL] $f appears in git history ($n commits)"; fail=1; }
    done
  fi
  # 3. High-entropy token shapes in TRACKED files only (gitignored .env won't
  #    ship, so don't false-positive on it). skip placeholders/tests.
  tok='(secret|token|api[_-]?key|password|cookie)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9_\-]{24,}'
  disc='[MNO][A-Za-z0-9_-]{23}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27}'
  if [ -d "$TARGET/.git" ]; then
    hits="$(cd "$TARGET" && scan_tracked "$tok|$disc" | grep -ivE 'test|fixture|mock|rendergate|placeholder|your_|REPLACE|<value>|token_hash|token_bytes' || true)"
  else
    hits="$(scan_tree "$tok|$disc" "$TARGET" | grep -ivE '\.example|\.template|test|fixture|mock|rendergate|placeholder|your_|REPLACE|<value>|token_hash|token_bytes' || true)"
  fi
  if [ -n "$hits" ]; then
    echo "  [warn] entropy/pattern matches (review — may be placeholders):"; echo "$hits" | sed 's/^/      /'
    fail=1
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "FAIL: potential secrets — rotate + scrub before publishing."; exit 1
fi
echo "PASS: no secrets in tree or history."; exit 0
