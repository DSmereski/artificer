#!/usr/bin/env bash
# Shared helpers for the public-release gate scripts.
# Each gate exits 0 = clean, 1 = findings (CI-blocking).
set -uo pipefail

# Prefer ripgrep; fall back to grep -r.
_have_rg() { command -v rg >/dev/null 2>&1; }

# scan_tree <regex> <target_dir> -> prints "path:count" per matching file.
# Honors RELEASE_EXCLUDES (space-separated dir/glob names) so a source-tree run
# skips dirs that will NOT ship (docs/plans, state, memory, the gates, etc.).
# On the real PUBLIC export those dirs don't exist, so the gate is strict there.
#
# scripts/release/ itself stays IN SCOPE here (it used to be excluded
# wholesale, which meant a gate script could ship a real leak right next to
# its own denylist code and never get caught). Instead RELEASE_EXCLUDE_FILES
# below excludes, BY NAME, only the specific gate files whose source
# deliberately embeds denylist regex LITERALS (home-dir path shapes,
# explicit-term lists) — those would otherwise self-match their own check.
: "${RELEASE_EXCLUDES:=.git node_modules .venv dist build state memory runs scratch \
    logs .ruff_cache .pytest_cache __pycache__ \
    docs/plans docs/release docs/superpowers docs/reviews}"
# Extra path globs to drop (build artifacts that never ship).
: "${RELEASE_EXCLUDE_GLOBS:=*.pyc *.log *.pyo}"
# Gate scripts whose OWN source is the denylist and would self-match its own
# check once scripts/release/ is back in scope (see above), plus the
# per-repo denylist DATA file itself (gitignored, intentionally holds real
# sensitive values, never ships — see check-personal.sh's CI note).
: "${RELEASE_EXCLUDE_FILES:=check-personal.sh check-nsfw.sh .personal-markers}"

# scan_tracked <regex> — grep ONLY git-tracked files (ignores gitignored .env,
# build cruft). Use for the secrets gate so a gitignored secret file that will
# NOT ship doesn't false-positive.
scan_tracked() {
  local regex="$1"
  git ls-files 2>/dev/null | grep -ivE '\.example$|\.template$' | while read -r f; do
    [ -f "$f" ] && grep -ilE "$regex" "$f" 2>/dev/null || true
  done
}

scan_tree() {
  local regex="$1" target="${2:-.}"
  if _have_rg; then
    local args=(--no-messages -i -l -e "$regex")
    for ex in $RELEASE_EXCLUDES; do args+=(--glob "!${ex}/**" --glob "!${ex}"); done
    for g in ${RELEASE_EXCLUDE_GLOBS:-}; do args+=(--glob "!${g}"); done
    for f in ${RELEASE_EXCLUDE_FILES:-}; do args+=(--glob "!**/${f}"); done
    rg "${args[@]}" "$target" 2>/dev/null || true
  else
    local ex_args=()
    for ex in $RELEASE_EXCLUDES; do ex_args+=(--exclude-dir="$(basename "$ex")"); done
    for g in ${RELEASE_EXCLUDE_GLOBS:-}; do ex_args+=(--exclude="$g"); done
    for f in ${RELEASE_EXCLUDE_FILES:-}; do ex_args+=(--exclude="$f"); done
    grep -rilE "${ex_args[@]}" "$regex" "$target" 2>/dev/null || true
  fi
}

# report <gate-name> <regex> <target>; sets GATE_FAIL=1 on any hit.
report() {
  local name="$1" regex="$2" target="${3:-.}"
  local hits; hits="$(scan_tree "$regex" "$target")"
  if [ -n "$hits" ]; then
    echo "  [FAIL] $name — matches in:"
    echo "$hits" | sed 's/^/      /'
    return 1
  fi
  echo "  [ok]   $name"
  return 0
}
