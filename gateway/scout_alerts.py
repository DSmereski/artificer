"""Background sampler that raises threshold alerts.

Runs on a separate task; wakes every SAMPLE_INTERVAL, fetches a Scout
snapshot, and emits an event + (optionally) an ntfy push when a tracked
metric crosses a threshold.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass


log = logging.getLogger("gateway.scout_alerts")


# Thresholds (hard-coded for v1; move into config if you want tuning).
GPU_TEMP_WARN = 80
GPU_TEMP_CRITICAL = 88
DISK_FREE_GB_LOW = 50
SAMPLE_INTERVAL = 15.0        # seconds
ALERT_COOLDOWN = 60 * 5        # 5 min; same alert won't fire again within that window


@dataclass
class _AlertState:
    last_fired: dict[str, float]


async def run(app_state) -> None:
    """Long-running task. Call from the FastAPI lifespan."""
    state = _AlertState(last_fired={})
    from gateway.routes import scout as scout_route   # local import to break cycle
    while True:
        try:
            snap = scout_route._snapshot()
        except Exception:  # noqa: BLE001
            log.exception("scout snapshot failed")
            await asyncio.sleep(SAMPLE_INTERVAL)
            continue

        _check(snap, state, app_state)
        await asyncio.sleep(SAMPLE_INTERVAL)


def _check(snap, state: _AlertState, app_state) -> None:
    import time
    now = time.monotonic()

    def _fire(key: str, *, title: str, message: str, priority: int) -> None:
        last = state.last_fired.get(key, 0.0)
        if now - last < ALERT_COOLDOWN:
            return
        state.last_fired[key] = now
        event = {
            "type": "scout_alert", "key": key, "title": title,
            "message": message, "priority": priority,
        }
        bus = app_state.event_bus
        if bus is not None:
            bus.publish(event)
        ntfy = app_state.ntfy
        if ntfy is not None and ntfy.enabled:
            # Schedule on the running loop; safe because this is called from
            # the same asyncio task that runs `run()` above.
            loop = asyncio.get_event_loop()
            loop.create_task(ntfy.publish(
                topic="ai-team-scout",
                title=title, message=message, priority=priority,
                tags=["warning"] if priority <= 3 else ["rotating_light"],
            ))
        log.info("scout alert fired: %s", key)

    for g in snap.gpus:
        if g.temp_c >= GPU_TEMP_CRITICAL:
            _fire(f"gpu{g.index}-critical",
                  title="GPU critical temp",
                  message=f"GPU {g.index} ({g.name}) at {g.temp_c}C",
                  priority=5)
        elif g.temp_c >= GPU_TEMP_WARN:
            _fire(f"gpu{g.index}-warn",
                  title="GPU hot",
                  message=f"GPU {g.index} at {g.temp_c}C",
                  priority=3)
    for d in snap.disks:
        if d.free_gb <= DISK_FREE_GB_LOW:
            _fire(f"disk{d.drive}-low",
                  title="Disk space low",
                  message=f"{d.drive} has {d.free_gb:.1f} GB free",
                  priority=4)
    for b in snap.bots:
        if not b.is_running:
            _fire(f"bot-{b.name}-down",
                  title="Bot offline",
                  message=f"{b.name} is not running",
                  priority=4)
