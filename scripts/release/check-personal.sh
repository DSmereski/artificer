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
# Usage: scripts/release/check-personal.sh [target_dir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
TARGET="${1:-.}"
echo "== check-personal == ($TARGET)"
fail=0

# Generic identity shape (no personal literals — safe to ship). Home-dir
# absolute paths almost always carry a real username.
report "home-dir absolute paths" 'C:\\\\Users\\\\[A-Za-z]|/home/[a-z][a-z0-9_-]+/|/Users/[A-Za-z]' "$TARGET" || fail=1
# (A generic private-IP check lives in the gitignored .personal-markers — it is
# noisy against legit example IPs like the Android emulator 10.0.2.2 and the
# RFC5737 docs ranges, so it is opt-in per-repo, not shipped on by default.)

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
