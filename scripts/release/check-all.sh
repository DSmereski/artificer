#!/usr/bin/env bash
# Run all three publish gates. Exit 0 only if ALL pass.
# This is the single command CI runs before a public push (P6).
#
# Usage: scripts/release/check-all.sh [target_dir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-.}"
rc=0
for gate in check-secrets check-personal check-nsfw; do
  echo "────────────────────────────────────────"
  bash "$HERE/$gate.sh" "$TARGET" || rc=1
done
echo "────────────────────────────────────────"
if [ "$rc" -eq 0 ]; then echo "✅ ALL GATES PASS — safe to publish."; else echo "❌ GATES FAILED — do NOT publish."; fi
exit "$rc"
