"""Scout threshold alerts."""

from __future__ import annotations

from gateway import scout_alerts as alerts
from gateway.events import EventBus
from gateway.routes import scout as scout_route


class _AppState:
    def __init__(self, bus, ntfy=None) -> None:
        self.event_bus = bus
        self.ntfy = ntfy


def _snap_with_temp(temp: int) -> scout_route.ScoutStatus:
    return scout_route.ScoutStatus(
        gpus=[scout_route.GPUInfo(
            index=0, name="test", temp_c=temp,
            vram_used_mb=0, vram_total_mb=16000,
            vram_used_pct=0.0, utilization_pct=0,
        )],
        disks=[],
        bots=[scout_route.BotHeartbeat(
            name="Hive", is_running=True, pid=1, uptime_seconds=10.0,
        )],
    )


def test_warn_and_critical_fire_at_thresholds() -> None:
    import asyncio
    bus = EventBus()
    app = _AppState(bus)

    async def _run() -> list[dict]:
        # Subscribe FIRST so we don't race with publish.
        q = await bus.subscribe("test")
        state = alerts._AlertState(last_fired={})
        alerts._check(_snap_with_temp(82), state, app)
        alerts._check(_snap_with_temp(90), state, app)
        events: list[dict] = []
        while len(events) < 2:
            events.append(await asyncio.wait_for(q.get(), timeout=1.0))
        return events

    events = asyncio.run(_run())
    keys = {e["key"] for e in events}
    assert "gpu0-warn" in keys
    assert "gpu0-critical" in keys


def test_alert_cooldown_suppresses_duplicate() -> None:
    import asyncio
    bus = EventBus()
    app = _AppState(bus)

    async def _run() -> None:
        q = await bus.subscribe("test")
        state = alerts._AlertState(last_fired={})
        alerts._check(_snap_with_temp(90), state, app)
        alerts._check(_snap_with_temp(90), state, app)   # cooldown should drop this
        first = await asyncio.wait_for(q.get(), timeout=1.0)
        assert first["key"] == "gpu0-critical"
        # The second should NOT appear — the cooldown filter dropped it.
        try:
            second = await asyncio.wait_for(q.get(), timeout=0.5)
            raise AssertionError(f"unexpected second event: {second}")
        except asyncio.TimeoutError:
            return

    asyncio.run(asyncio.wait_for(_run(), timeout=3.0))


def test_bot_down_fires() -> None:
    import asyncio
    bus = EventBus()
    app = _AppState(bus)

    snap = scout_route.ScoutStatus(
        gpus=[], disks=[],
        bots=[scout_route.BotHeartbeat(
            name="Maggy", is_running=False, pid=None, uptime_seconds=None,
        )],
    )

    async def _run() -> None:
        q = await bus.subscribe("test")
        state = alerts._AlertState(last_fired={})
        alerts._check(snap, state, app)
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event["key"] == "bot-Maggy-down"

    asyncio.run(asyncio.wait_for(_run(), timeout=3.0))
