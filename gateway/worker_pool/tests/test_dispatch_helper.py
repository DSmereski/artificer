"""Tests for dispatch_and_wait — async helper façade."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gateway.worker_pool.dispatcher import Dispatcher
from gateway.worker_pool.dispatch_helper import (
    DispatchError,
    DispatchTimeout,
    dispatch_and_wait,
)


@pytest.fixture
def disp(tmp_path: Path) -> Dispatcher:
    return Dispatcher.open(tmp_path / "hive_jobs.db")


@pytest.mark.asyncio
async def test_dispatch_and_wait_resolves_on_complete(disp: Dispatcher) -> None:
    """Caller awaits, another task simulates a node delivering the result."""
    async def worker() -> None:
        # Tiny pause so the helper has time to register its waiter.
        await asyncio.sleep(0.05)
        # Find the queued job and "run" it.
        queued = disp.get_queued()
        assert queued, "expected a queued job"
        job = queued[0]
        disp.assign_to_node(job.id, node_id="n_test")
        disp.complete(
            job.id, result={"output": "ok"}, duration_ms=10, node_id="n_test",
        )

    task = asyncio.create_task(worker())
    result = await dispatch_and_wait(
        disp,
        kind="t.echo",
        payload={"x": 1},
        required_caps=(),
        timeout_s=2.0,
    )
    await task
    assert result["status"] == "done"
    assert result["output"] == {"output": "ok"}
    assert result["duration_ms"] == 10


@pytest.mark.asyncio
async def test_dispatch_and_wait_propagates_adapter_error(
    disp: Dispatcher,
) -> None:
    async def worker() -> None:
        await asyncio.sleep(0.05)
        job = disp.get_queued()[0]
        disp.assign_to_node(job.id, node_id="n_test")
        disp.report_adapter_error(
            job.id, error="bad input", duration_ms=2, node_id="n_test",
        )

    task = asyncio.create_task(worker())
    with pytest.raises(DispatchError, match="bad input"):
        await dispatch_and_wait(
            disp, kind="t.echo", payload={}, required_caps=(),
            timeout_s=2.0,
        )
    await task


@pytest.mark.asyncio
async def test_dispatch_and_wait_times_out(disp: Dispatcher) -> None:
    with pytest.raises(DispatchTimeout):
        await dispatch_and_wait(
            disp, kind="t.echo", payload={}, required_caps=(),
            timeout_s=0.1,
        )


@pytest.mark.asyncio
async def test_dispatch_and_wait_propagates_failure(
    disp: Dispatcher,
) -> None:
    """When fail() reaches max_attempts, the helper raises DispatchError."""
    async def worker() -> None:
        await asyncio.sleep(0.05)
        job = disp.get_queued()[0]
        # max_attempts=1 means a single fail() goes terminal.
        disp.assign_to_node(job.id, node_id="n_test")
        disp.fail(job.id, error="infrastructure went bang")

    task = asyncio.create_task(worker())
    with pytest.raises(DispatchError, match="infrastructure went bang"):
        await dispatch_and_wait(
            disp, kind="t.echo", payload={}, required_caps=(),
            timeout_s=2.0, max_attempts=1,
        )
    await task
