"""Scout daemon: headless watchdog + system monitoring.

Replaces the old Discord-bot Scout. No chat persona, no LLM. Just:
- Process supervision (auto-restart of Terry / gateway)
- GPU temp + VRAM monitoring (ntfy push on critical temp)
- Game detection (mute notifications when gaming)
- Disk space checks (ntfy push when free space below DISK_WARN_GB)
- Localhost RPC for the M2 Sysmon helper to query state
- ntfy push via urllib (best-effort, swallows failures); URL/topic
  are read from NTFY_URL / NTFY_TOPIC env vars (config/.env)
"""
