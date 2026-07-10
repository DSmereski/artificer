#!/usr/bin/env bash
# Chain-run scenarios sequentially using `wait` on each PID.
# pgrep is unreliable on Git Bash for python.exe — wait $PID is exact.
set -u
TOKEN="${1:?token required}"
shift
SCNS=("$@")
# Repo root = parent of this script's dir; log dir is configurable.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOGDIR="${HIVE_LOG_DIR:-/tmp/hive}"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/chain_scn.log"
echo "[$(date -Iseconds)] chain start: ${SCNS[*]}" | tee -a "$LOG"

cd "$REPO"
for s in "${SCNS[@]}"; do
    echo "[$(date -Iseconds)] launching $s" | tee -a "$LOG"
    python -u scripts/run_scenarios.py --scenario "$s" --token "$TOKEN" \
        --per-turn-timeout 300 > "$LOGDIR/scn${s}.log" 2>&1 &
    PID=$!
    echo "[$(date -Iseconds)] $s pid=$PID — waiting" | tee -a "$LOG"
    wait $PID
    EXIT=$?
    echo "[$(date -Iseconds)] $s exited $EXIT" | tee -a "$LOG"
done

echo "[$(date -Iseconds)] chain done" | tee -a "$LOG"
