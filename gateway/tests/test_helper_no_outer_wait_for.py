"""Phase B.3 of #476: dropping the outer asyncio.wait_for in
_run_helper. The helper's own internal wait_for(self.timeout_s) at
helpers/base.py:430 still bounds runtime; the coordinator-level cap was
producing latency_ms:0 'turn budget timeout' rows that obscured the
real failure mode (researcher was being aborted while still computing).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from gateway.helpers.base import HelperResult, HelperTask
from gateway.hive_coordinator import HiveCoordinator, TurnBudget
from gateway.model_catalog import load_catalog


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REAL_CATALOG = _PROJECT_ROOT / "config" / "model_catalog.yaml"


class _SlowResearcher:
    """Sleeps then returns success. No internal wait_for — simulates a
    helper that just takes a long time."""

    timeout_s = 60.0

    def __init__(self, sleep_s: float = 2.0) -> None:
        self._sleep_s = sleep_s

    async def invoke(self, task: HelperTask) -> HelperResult:
        await asyncio.sleep(self._sleep_s)
        return HelperResult(
            role=task.role, model_id="planner-qwen",
            output={"summary": "done"},
            confidence="high",
            tokens_in=1, tokens_out=2,
            latency_ms=int(self._sleep_s * 1000),
            parent_id=task.parent_id,
        )


def _build_coord(helpers: dict) -> HiveCoordinator:
    catalog = load_catalog(_REAL_CATALOG)
    return HiveCoordinator(catalog, helpers, budget=TurnBudget())


@pytest.mark.asyncio
async def test_run_helper_completes_when_deadline_already_passed():
    """Old behaviour: deadline 5s in past → wait_for(remaining=1.0s)
    cancels the helper after 1s with error='turn budget timeout' and
    latency_ms:0 was reported on the wire (since the wrapped result
    didn't carry the helper's own latency).

    New behaviour (#476 Phase B.3): no outer wait_for; the helper runs
    to completion and returns a real result. Internal helper-level
    timeout still bounds genuinely-stuck runs.
    """
    helpers = {"researcher": _SlowResearcher(sleep_s=2.0)}
    coord = _build_coord(helpers)
    task = HelperTask(role="researcher", goal="x", inputs={}, parent_id="t1")
    deadline = time.monotonic() - 5.0  # 5s in the past
    result = await coord._run_helper(task, deadline)
    assert result.error is None or result.error == ""
    assert result.output.get("summary") == "done"
    assert result.latency_ms >= 2000


@pytest.mark.asyncio
async def test_run_helper_propagates_cancellation():
    """CancelledError must still propagate (WS disconnect, parent
    shutdown). Removing wait_for must not break cancellation."""
    helpers = {"researcher": _SlowResearcher(sleep_s=10.0)}
    coord = _build_coord(helpers)
    task = HelperTask(role="researcher", goal="x", inputs={}, parent_id="t1")
    inner = asyncio.create_task(
        coord._run_helper(task, time.monotonic() + 60),
    )
    await asyncio.sleep(0.1)
    inner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await inner
