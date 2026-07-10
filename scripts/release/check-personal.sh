#!/usr/bin/env bash
# Gate: NO personal/identity data in the public tree.
# Exit 0 = clean, 1 = findings. CI-required (P2/P6).
#
# IMPORTANT: this script ships NO literal personal markers. (A previous version
# embedded the owner's name/IPs/device-ids and even a reused password as grep
# literals — and so became the very leak it was meant to prevent.) Repo-specific
# markers live in an UNCOMMITTED, gitignored file:
#   scripts/release/.personal-markers   — one  label|regex  per line.
# Generic identity shapes are always checked.
#
# CI NOTE: because .personal-markers is gitignored, a plain `actions/checkout`
# in CI will NEVER have it — the exact-literal owner markers below are
# effectively skipped there and only the generic shape checks run. To close
# that gap, deliver the file's contents via a repo secret and write it out as
# a step before this gate runs, e.g.:
#   - run: printf '%s\n' "$PERSONAL_MARKERS" > scripts/release/.personal-markers
#     env: { PERSONAL_MARKERS: ${{ secrets.PERSONAL_MARKERS }} }
# Until that's wired up, treat the generic checks as the CI-enforced floor.
#
# Usage: scripts/release/check-personal.sh [target_dir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
TARGET="${1:-.}"
echo "== check-personal == ($TARGET)"
fail=0

# ---------------------------------------------------------------- absolute paths
# Generic identity shape (no personal literals — safe to ship). Absolute
# Windows drive paths (any drive letter, not just C:) and POSIX/macOS home
# dirs almost always carry a real username or a personal dev-root folder
# name. Backslash count is 1-OR-2 so this matches BOTH raw-string/markdown/
# .ps1 renderings (single `\`) AND Python-escaped-string renderings (`\\`) of
# the same path. Segments must be >=2 chars so it doesn't snag on ordinary
# prose like "...: \n\n" (a single letter + colon + escaped-newline looks
# byte-for-byte like a 1-letter drive segment).
#
# ALLOWLIST: well-known generic/system top-level dirs that carry no personal
# signal by themselves (Program Files won't match anyway — the internal space
# breaks the segment — but is listed for clarity). Deliberately does NOT
# allowlist "Users" — C:\Users\<name>\... is exactly the risky shape.
_DRIVE_PATH_RE='(^|[^A-Za-z0-9])[A-Za-z]:\\{1,2}[A-Za-z0-9_-]{2,}\\{1,2}'
_DRIVE_ALLOW_RE='[A-Za-z]:\\{1,2}(Windows|Program ?Files( ?\(x86\))?|ProgramData|Temp|tmp|TEMP|Backups)\\{1,2}'
_POSIX_HOME_RE='/home/[a-z][a-z0-9_-]+/|/Users/[A-Za-z]'
# Git-Bash / WSL drive-MOUNT form of the same risky roots: `/c/Users/<name>`,
# `/c/Projects/...`, `/mnt/c/Users/...`. This is byte-for-byte the same leak
# as the Windows-backslash form above but with forward slashes, so the
# backslash detector misses it — catch it explicitly. Scoped to the two
# high-signal roots (Users = a real username; Projects = a personal dev
# root) so ordinary POSIX paths like /usr, /tmp, /etc don't false-positive.
_POSIX_MOUNT_RE='/(mnt/)?[a-z]/(Users|Projects)/'

scan_absolute_paths() {
  local target="${1:-.}" hits
  if _have_rg; then
    local args=(--no-messages -n -i -e "$_DRIVE_PATH_RE" -e "$_POSIX_HOME_RE" -e "$_POSIX_MOUNT_RE")
    for ex in $RELEASE_EXCLUDES; do args+=(--glob "!${ex}/**" --glob "!${ex}"); done
    for g in ${RELEASE_EXCLUDE_GLOBS:-}; do args+=(--glob "!${g}"); done
    for f in ${RELEASE_EXCLUDE_FILES:-}; do args+=(--glob "!**/${f}"); done
    hits="$(rg "${args[@]}" "$target" 2>/dev/null || true)"
  else
    local ex_args=()
    for ex in $RELEASE_EXCLUDES; do ex_args+=(--exclude-dir="$(basename "$ex")"); done
    for g in ${RELEASE_EXCLUDE_GLOBS:-}; do ex_args+=(--exclude="$g"); done
    for f in ${RELEASE_EXCLUDE_FILES:-}; do ex_args+=(--exclude="$f"); done
    hits="$(grep -rniE "${ex_args[@]}" -e "$_DRIVE_PATH_RE" -e "$_POSIX_HOME_RE" -e "$_POSIX_MOUNT_RE" "$target" 2>/dev/null || true)"
  fi
  printf '%s\n' "$hits" | grep -viE "$_DRIVE_ALLOW_RE" | grep -v '^[[:space:]]*$'
}

hits="$(scan_absolute_paths "$TARGET")"
if [ -n "$hits" ]; then
  echo "  [FAIL] absolute personal/home paths — matches in:"
  echo "$hits" | sed 's/^/      /'
  fail=1
else
  echo "  [ok]   absolute personal/home paths"
fi

# ---------------------------------------------------------------- private-IP / Discord-snowflake shapes
# Generic pattern check (not repo-specific literals): flags real-looking
# RFC1918 / Tailscale-CGNAT private IPv4 addresses and Discord-snowflake-
# shaped IDs (17-19 digits) so a future leak is still caught even when
# .personal-markers is absent (see CI NOTE above). Deliberately excludes:
#   - CIDR network literals (a trailing /8, /10, /12, /16, ... on the same
#     line) — this repo's OWN private-range allow-lists (gateway/routes/
#     admin.py, gateway/config.py, gateway/asset_importer.py) legitimately
#     embed these ranges.
#   - tests/ dirs and test_*/smoke_* files — placeholder fixture IPs.
#   - the well-known Android emulator host loopback 10.0.2.2.
#   - the scrubbed all-same-digit placeholder ID (e.g. 000000000000000000)
#     used as the sanitized OWNER_ID/bot-ID default.
_PRIVATE_IP_RE='\b(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3})\b'
_SNOWFLAKE_RE='\b[0-9]{17,19}\b'

scan_ip_snowflake() {
  local target="${1:-.}" hits
  if _have_rg; then
    local args=(--no-messages -n -e "$_PRIVATE_IP_RE" -e "$_SNOWFLAKE_RE")
    for ex in $RELEASE_EXCLUDES; do args+=(--glob "!${ex}/**" --glob "!${ex}"); done
    for g in ${RELEASE_EXCLUDE_GLOBS:-}; do args+=(--glob "!${g}"); done
    for f in ${RELEASE_EXCLUDE_FILES:-}; do args+=(--glob "!**/${f}"); done
    args+=(--glob '!**/tests/**' --glob '!**/test_*' --glob '!**/smoke_*')
    hits="$(rg "${args[@]}" "$target" 2>/dev/null || true)"
  else
    local ex_args=()
    for ex in $RELEASE_EXCLUDES; do ex_args+=(--exclude-dir="$(basename "$ex")"); done
    for g in ${RELEASE_EXCLUDE_GLOBS:-}; do ex_args+=(--exclude="$g"); done
    for f in ${RELEASE_EXCLUDE_FILES:-}; do ex_args+=(--exclude="$f"); done
    hits="$(grep -rnE "${ex_args[@]}" \
      --exclude-dir=.git --exclude-dir=tests --exclude-dir=node_modules \
      --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=venv \
      --exclude='test_*' --exclude='smoke_*' \
      -e "$_PRIVATE_IP_RE" -e "$_SNOWFLAKE_RE" "$target" 2>/dev/null || true)"
  fi
  printf '%s\n' "$hits" \
    | grep -vE '/[0-9]{1,3}\b' \
    | grep -v '10\.0\.2\.2' \
    | grep -vE '0{17,19}|1{17,19}' \
    | grep -v '^[[:space:]]*$'
}

hits="$(scan_ip_snowflake "$TARGET")"
if [ -n "$hits" ]; then
  echo "  [FAIL] private-IP / Discord-snowflake shape — matches in:"
  echo "$hits" | sed 's/^/      /'
  fail=1
else
  echo "  [ok]   private-IP / Discord-snowflake shape"
fi

# ---------------------------------------------------------------- repo-specific markers
# Repo-specific markers from the gitignored file (if present).
MARKERS="$HERE/.personal-markers"
if [ -f "$MARKERS" ]; then
  while IFS='|' read -r label pat; do
    [ -z "${label// }" ] && continue
    case "$label" in \#*) continue;; esac
    report "$label" "$pat" "$TARGET" || fail=1
  done < "$MARKERS"
else
  echo "  --  no scripts/release/.personal-markers (gitignored) — generic checks only"
fi

if [ "$fail" -ne 0 ]; then
  echo "FAIL: personal data present — scrub before publishing."; exit 1
fi
echo "PASS: no personal markers."; exit 0
