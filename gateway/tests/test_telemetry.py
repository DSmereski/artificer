"""Tests for the M6.3 turn telemetry buffer."""

from __future__ import annotations

from gateway.turn_telemetry import TurnRecord, TurnTelemetry


def _record(turn_id: str = "tk-1", **kw) -> TurnRecord:
    base = dict(
        ts=1.0, turn_id=turn_id, bot="terry",
        user_msg_preview="hi", helpers_used=["planner"],
        total_tokens=100, total_latency_ms=500,
        blocked=False, error=None, actions=[],
    )
    base.update(kw)
    return TurnRecord(**base)


def test_telemetry_records_and_returns_recent():
    tel = TurnTelemetry(max_records=10)
    for i in range(3):
        tel.record(_record(turn_id=f"tk-{i}"))
    last = tel.last(n=10)
    assert [r.turn_id for r in last] == ["tk-0", "tk-1", "tk-2"]


def test_telemetry_caps_at_max():
    tel = TurnTelemetry(max_records=3)
    for i in range(10):
        tel.record(_record(turn_id=f"tk-{i}"))
    last = tel.last(n=10)
    assert len(last) == 3
    assert [r.turn_id for r in last] == ["tk-7", "tk-8", "tk-9"]


def test_telemetry_to_jsonable():
    tel = TurnTelemetry()
    tel.record(_record(turn_id="t1", helpers_used=["planner", "coder"]))
    out = tel.to_jsonable(n=5)
    assert isinstance(out, list)
    assert out[0]["turn_id"] == "t1"
    assert out[0]["helpers_used"] == ["planner", "coder"]


def test_telemetry_clear():
    tel = TurnTelemetry()
    tel.record(_record())
    tel.clear()
    assert tel.last() == []
