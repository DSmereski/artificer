---
name: hive-e2e-chat
description: Drive a multi-turn end-to-end chat conversation against the running Hive gateway via WebSocket and dump the frames for analysis. Use when user says "run an e2e chat", "drive a conversation", "test the chat WS", or wants to verify Hive's coordinator end-to-end after a change.
user_invocable: true
---

# Hive E2E Chat Driver

Drive a scripted multi-turn chat against the live gateway over WebSocket and capture every frame for post-hoc analysis. Useful for verifying:
- coordinator turn loop integrity
- synthesizer doesn't drop replies
- WS keepalive holds
- memory tier (verbatim → mid → recall) interactions across turns

## Prerequisites

- Gateway must be running (`scripts/start-all.ps1` or `python -m gateway`).
- Auth token. Get it from the Flutter app's pairing flow, or from `state/devices/owner.json`.

## How to run

```bash
cd path/to/hive && \
python scripts/e2e_chat_driver.py \
  --token "<BEARER_TOKEN>" \
  --host 127.0.0.1:8766 \
  --out /tmp/hive/e2e_chat_$(date +%Y%m%d_%H%M%S).json
```

The script runs an 8-turn predefined SCRIPT (see top of `scripts/e2e_chat_driver.py`) and writes one JSON object per turn to the `--out` path with all WS frames.

## What to look for in the dump

- **Reply present and non-empty** in every turn (synth blindness regression).
- **`<think>...</think>` blocks stripped** before the user-facing reply.
- **`[ASK_USER]` / `[CONFIRM_IMAGE]` markers on their own line**, not embedded in JSON.
- **Helper outputs visible in turn logs** (planner picked at least one helper).
- **No `RuntimeError`** in any frame.

## Troubleshooting

- **Connection refused** → gateway isn't up. Run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts/start-all.ps1"` from the repo root, wait 15s, check `$HIVE_LOG_DIR/gateway.log` (defaults under your temp dir).
- **401** → bad token. Re-pair or read fresh one from `state/devices/`.
- **Driver hangs after a turn** → check for `[ASK_USER]` waiting on chip-button input. The script auto-acks but only for known questions; new ones may need adding to SCRIPT.
- **WS closes mid-turn with code 1011** → keepalive issue. Check `gateway/__main__.py` `_WS_PING_INTERVAL_S` / `_WS_PING_TIMEOUT_S` (should be 30 / 90).

## Related

- `gateway/__main__.py` — WS keepalive config
- `gateway/routes/chat.py` — WS endpoint
- `gateway/hive_coordinator.py` — turn loop
