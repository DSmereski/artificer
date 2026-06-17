#!/usr/bin/env bash
# Gate: NO NSFW / uncensored content or models in the public tree.
# Exit 0 = clean, 1 = findings. CI-required (#164/P6).
#
# Usage: scripts/release/check-nsfw.sh [target_dir]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/lib.sh"
TARGET="${1:-.}"
echo "== check-nsfw == ($TARGET)"
fail=0

# Uncensored/abliterated model ids — must not be a shipped default.
report "uncensored/abliterated models" 'abliterated|-uncensored|huihui_ai'  "$TARGET" || fail=1
# Explicit terms — genuine NSFW content only. A bare `nsfw` boolean/field is a
# legit SAFETY filter (the image catalog filters NSFW OUT), so it is NOT flagged.
report "explicit terms"  '\bporn\b|\bnude\b|\blewd\b|onlyfans|civitai\.red|sex-' "$TARGET" || fail=1

# Generated-content dirs must ship EMPTY (no media artifacts).
for d in state/content state/media image_research/out content/generated; do
  if [ -d "$TARGET/$d" ] && [ -n "$(find "$TARGET/$d" -type f \
      \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \
         -o -iname '*.mp4' -o -iname '*.safetensors' -o -iname '*.mp3' \) 2>/dev/null)" ]; then
    echo "  [FAIL] generated media present in $d (must ship empty)"; fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "FAIL: NSFW/uncensored content present — scrub before publishing."; exit 1
fi
echo "PASS: SFW + no uncensored models."; exit 0
