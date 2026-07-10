---
name: hive-restart-gateway
description: Restart just the Hive gateway process (without touching Terry or Scout). Use when user says "restart the gateway", "reload the gateway", "bounce the gateway", or after backend code changes that need to take effect.
user_invocable: true
---

# Restart the Hive Gateway

Stops and restarts the Python `gateway` process bound to ports 8766 (loopback + Tailscale `100.x`). Leaves the Terry voice/image bot and Scout watchdog running.

## How to do it

```bash
# 1. Find the gateway PID
tasklist /FI "IMAGENAME eq python.exe" /V 2>&1 | grep -i gateway

# 2. Kill it (replace <PID>)
taskkill /F /PID <PID>

# 3. Restart in background, redirect stderr+stdout to log
cd path/to/hive && \
  nohup python -m gateway > /tmp/hive/gateway.log 2> /tmp/hive/gateway.log.err &
```

Or, use the full PowerShell launcher (starts gateway + Terry + Scout; idempotent — skips already-running processes):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts/start-all.ps1"
```

## How to verify it came up

Wait ~3 seconds, then check the tail of `$HIVE_LOG_DIR/gateway.log.err`:

```bash
tail -15 /tmp/hive/gateway.log.err
```

Expect:
- `gateway.app: skill registry loaded: ...`
- `gateway.app: hive coordinator built with N helpers`
- `gateway.app: image catalog loaded (...)`
- `gateway.app: gateway ready with bots: terry`
- `Uvicorn running on http://127.0.0.1:8766`
- `Uvicorn running on http://127.0.0.1:8766`

If you don't see the last two lines, the gateway didn't bind properly.

## Common failure modes

- **`Address already in use`** → old process didn't die. `netstat -ano | findstr :8766` to find the lingerer, `taskkill /F /PID <PID>`.
- **Crashes on import** → usually a Python syntax error in the code you just changed. Check the first lines of `gateway.log.err`.
- **Lifespan hangs** → vault_writer daemon didn't come up. Check if it's running: `tasklist | grep vault_writer` and look at `$HIVE_LOG_DIR/vault_writer.log`.

## Don't restart unnecessarily

Most code changes don't need a restart — the gateway hot-reloads on file changes if you ran it with `--reload`. Restart only when:
- you changed lifespan / startup code
- you changed config files
- you're chasing a state-corruption bug
