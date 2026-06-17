"""Phase B.5 of #476 + #484 hard-cancel: synth-on-ready gate.
_dispatch fires synth on whatever helpers have returned by
``budget.synth_gate_s``; pending tasks are HARD-CANCELLED so synth
gets the Ollama slot to itself (single NUM_PARALLEL=1 slot on
planner-qwen). Cancel propagates through ``asyncio.wait_for`` into
httpx, freeing the Ollama HTTP request before synth fires."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from gateway.event_emitter import ListEmitter
from gateway.helpers.base import HelperResult, HelperTask
from gateway.hive_coordinator import HiveCoordinator, TurnBudget, TurnContext
from gateway.model_catalog import load_catalog


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REAL_CATALOG = _PROJECT_ROOT / "config" / "model_catalog.yaml"


class _FastHelper:
    timeout_s = 60.0

    def __init__(self, role: str) -> None:
        self._role = role

    async def invoke(self, task: HelperTask) -> HelperResult:
        await asyncio.sleep(0.1)
        return HelperResult(
            role=task.role, model_id="fake",
            latency_ms=100, parent_id=task.parent_id,
            output={"summary": "ok"}, confidence="high",
        )


class _SlowHelper:
    """Returns successfully but only after `sleep_s` seconds."""

    timeout_s = 60.0

    def __init__(self, role: str, sleep_s: float = 5.0) -> None:
        self._role = role
        self._sleep_s = sleep_s

    async def invoke(self, task: HelperTask) -> HelperResult:
        await asyncio.sleep(self._sleep_s)
        return HelperResult(
            role=task.role, model_id="gemma3-4b",
            latency_ms=int(self._sleep_s * 1000),
            parent_id=task.parent_id,
            output={"summary": "slow but real"}, confidence="medium",
        )


def _build_coord(helpers: dict, *, gate: float) -> HiveCoordinator:
    catalog = load_catalog(_REAL_CATALOG)
    return HiveCoordinator(
        catalog, helpers, budget=TurnBudget(synth_gate_s=gate),
    )


def _ctx() -> TurnContext:
    return TurnContext(
        bot="terry", user_id=1, device_id="dev1",
        user_msg="x", thread_id="t",
        history_digest="",
        available_helpers=["librarian", "researcher"],
    )


@pytest.mark.asyncio
async def test_dispatch_returns_all_when_helpers_fast():
    """Baseline: with fast helpers, all return well before the gate;
    no detachment, no late events."""
    coord = _build_coord(
        helpers={
            "librarian": _FastHelper("librarian"),
            "researcher": _FastHelper("researcher"),
        },
        gate=2.0,
    )
    delegations = [
        {"role": "librarian", "goal": "g"},
        {"role": "researcher", "goal": "g"},
    ]
    em = ListEmitter()
    t0 = time.monotonic()
    results = await coord._dispatch(
        delegations, _ctx(), "tid",
        time.monotonic() + 60, em,
    )
    elapsed = time.monotonic() - t0
    assert len(results) == 2
    assert all(r.error is None or r.error == "" for r in results)
    assert elapsed < 1.0  # well under gate
    assert len(coord._late_helper_tasks) == 0


@pytest.mark.asyncio
async def test_dispatch_fires_at_gate_with_slow_helper():
    """When one helper exceeds the gate, _dispatch returns at the gate
    with whatever has come back; the slow one is hard-cancelled (#484)
    so synth doesn't contend for the Ollama slot."""
    coord = _build_coord(
        helpers={
            "librarian": _FastHelper("librarian"),
            "researcher": _SlowHelper("researcher", sleep_s=5.0),
        },
        gate=1.0,
    )
    delegations = [
        {"role": "librarian", "goal": "g"},
        {"role": "researcher", "goal": "g"},
    ]
    em = ListEmitter()
    t0 = time.monotonic()
    results = await coord._dispatch(
        delegations, _ctx(), "tid",
        time.monotonic() + 60, em,
    )
    elapsed = time.monotonic() - t0
    # Gate fires at ~1s; only fast librarian made it. Drain of cancelled
    # task is bounded by httpx teardown (≪ 100ms in tests because the
    # fake helper is just an asyncio.sleep, no httpx).
    assert 0.9 < elapsed < 1.8, f"elapsed={elapsed}"
    roles = {r.role for r in results}
    assert "librarian" in roles
    assert "researcher" not in roles
    # No detach: slow researcher was hard-cancelled.
    assert len(coord._late_helper_tasks) == 0


@pytest.mark.asyncio
async def test_slow_helper_is_cancelled_at_gate():
    """The slow helper's task ends in CancelledError, NOT a HelperResult.
    Verifies the cancel propagated (so any in-flight Ollama HTTP request
    would be torn down on production)."""
    slow = _SlowHelper("researcher", sleep_s=2.0)
    coord = _build_coord(
        helpers={"researcher": slow},
        gate=0.3,
    )
    em = ListEmitter()
    t0 = time.monotonic()
    results = await coord._dispatch(
        [{"role": "researcher", "goal": "g"}],
        _ctx(), "tid",
        time.monotonic() + 60, em,
    )
    elapsed = time.monotonic() - t0
    assert len(results) == 0  # gate fired first
    # Slow helper was cancelled before its 2s sleep finished.
    assert elapsed < 1.5, f"cancel didn't drain quickly: {elapsed}s"
    # No helper.late events — the late-helper detach pattern is gone (#484).
    late = [e for e in em.events if e.type == "helper.late"]
    assert not late, f"unexpected helper.late events: {late}"
