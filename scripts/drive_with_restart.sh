#!/usr/bin/env bash
# Resilient wrapper around the StarCraft build driver. The in-process
# dispatcher has hit an intermittent silent crash on Windows (asyncio
# subprocess teardown). This loop restarts it until the board drains
# or we hit a restart cap, cleaning the stale PID lock between runs.
set -u

REPO="/c/Projects/Ai-Team"
LOG="/c/tmp/ai-team/sc_improve_restart.log"
PIDLOCK="/c/tmp/ai-team/starcraft/driver.pid"
MAX_RESTARTS=40

cd "$REPO" || exit 1
export PYTHONPATH=.
export PYTHONIOENCODING=utf-8

restarts=0
while [ "$restarts" -lt "$MAX_RESTARTS" ]; do
  rm -f "$PIDLOCK"
  echo "=== driver start (restart #$restarts) $(date) ===" >> "$LOG"
  python -u scripts/spawn_starcraft_build.py --drive >> "$LOG" 2>&1
  rc=$?
  echo "=== driver exited rc=$rc $(date) ===" >> "$LOG"
  # rc 0 = board drained cleanly. Stop.
  if [ "$rc" -eq 0 ]; then
    echo "=== board drained, stopping wrapper ===" >> "$LOG"
    break
  fi
  # rc 3 = another driver holds the lock. Stop (don't fight it).
  if [ "$rc" -eq 3 ]; then
    echo "=== lock held by another driver, stopping wrapper ===" >> "$LOG"
    break
  fi
  restarts=$((restarts + 1))
  sleep 3
done
echo "=== wrapper done after $restarts restarts $(date) ===" >> "$LOG"
