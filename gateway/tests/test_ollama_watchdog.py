"""Pin behavior of the mid-run Ollama residency watchdog (#473)."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from gateway.ollama_probe import ProbeResult
from gateway.ollama_watchdog import watchdog_loop


def _gpu(name: str = "planner-qwen:latest") -> ProbeResult:
    return ProbeResult(
        ok=True, processor="gpu", gpu_pct=100.0,
        message=f"{name} 100% GPU", model_name=name,
    )


def _cpu(name: str = "planner-qwen:latest") -> ProbeResult:
    return ProbeResult(
        ok=False, processor="cpu", gpu_pct=0.0,
        message=f"{name} on CPU", model_name=name,
    )


def _mixed() -> ProbeResult:
    return ProbeResult(
        ok=False, processor="mixed", gpu_pct=40.0,
        message="40% GPU / 60% CPU", model_name="planner-qwen:latest",
    )


def _unreachable() -> ProbeResult:
    return ProbeResult(
        ok=False, processor="unreachable", gpu_pct=0.0,
        message="ollama unreachable", model_name="",
    )


class _FakeProbe:
    """Async callable returning preset verdicts, then cancelling the loop."""

    def __init__(self, verdicts: list[ProbeResult], stop_after: int) -> None:
        self.verdicts = list(verdicts)
        self.calls = 0
        self.stop_after = stop_after

    async def __call__(self, _prefix: str, **__) -> ProbeResult:
        self.calls += 1
        if self.calls > self.stop_after:
            raise asyncio.CancelledError()
        return self.verdicts[min(self.calls - 1, len(self.verdicts) - 1)]


async def _run_loop(
    *,
    verdicts: list[ProbeResult],
    aborts: list[tuple[str, str]],
    abort_on_bad_verdict: bool = True,
    stop_after: int | None = None,
) -> SimpleNamespace:
    app_state = SimpleNamespace(ollama_probe_result=None)
    probe = _FakeProbe(verdicts, stop_after=stop_after or len(verdicts))

    def _abort(proc: str, msg: str) -> None:
        aborts.append((proc, msg))

    task = asyncio.create_task(watchdog_loop(
        app_state,
        interval_s=0.0,
        abort_on_bad_verdict=abort_on_bad_verdict,
        probe_fn=probe,
        abort_fn=_abort,
    ))
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, BaseException):
            pass
    app_state.probe = probe
    return app_state


@pytest.mark.asyncio
async def test_gpu_verdict_does_not_abort() -> None:
    aborts: list[tuple[str, str]] = []
    state = await _run_loop(
        verdicts=[_gpu(), _gpu()],
        aborts=aborts,
        stop_after=2,
    )
    assert aborts == []
    assert state.ollama_probe_result.processor == "gpu"


@pytest.mark.asyncio
async def test_cpu_verdict_aborts_with_flag(caplog) -> None:
    aborts: list[tuple[str, str]] = []
    with caplog.at_level(logging.CRITICAL, logger="gateway.ollama_watchdog"):
        state = await _run_loop(
            verdicts=[_cpu()],
            aborts=aborts,
            abort_on_bad_verdict=True,
            stop_after=1,
        )
    assert aborts, "expected abort on first CPU verdict"
    assert aborts[0][0] == "cpu"
    assert state.ollama_probe_result.processor == "cpu"
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any("drifted to cpu" in r.getMessage() for r in crit)


@pytest.mark.asyncio
async def test_mixed_verdict_aborts_with_flag() -> None:
    aborts: list[tuple[str, str]] = []
    await _run_loop(
        verdicts=[_mixed()],
        aborts=aborts,
        abort_on_bad_verdict=True,
        stop_after=1,
    )
    assert aborts and aborts[0][0] == "mixed"


@pytest.mark.asyncio
async def test_cpu_verdict_no_abort_with_flag_off() -> None:
    aborts: list[tuple[str, str]] = []
    await _run_loop(
        verdicts=[_cpu()],
        aborts=aborts,
        abort_on_bad_verdict=False,
        stop_after=1,
    )
    assert aborts == []


@pytest.mark.asyncio
async def test_repeat_cpu_verdict_aborts_only_once() -> None:
    """First CPU verdict aborts, second CPU verdict in a row should not."""
    aborts: list[tuple[str, str]] = []
    await _run_loop(
        verdicts=[_cpu(), _cpu(), _cpu()],
        aborts=aborts,
        abort_on_bad_verdict=True,
        stop_after=3,
    )
    assert len(aborts) == 1, f"expected 1 abort, got {len(aborts)}"


@pytest.mark.asyncio
async def test_recovery_logs_at_warning(caplog) -> None:
    aborts: list[tuple[str, str]] = []
    with caplog.at_level(logging.WARNING, logger="gateway.ollama_watchdog"):
        await _run_loop(
            verdicts=[_cpu(), _gpu()],
            aborts=aborts,
            abort_on_bad_verdict=False,
            stop_after=2,
        )
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("recovered to GPU" in r.getMessage() for r in warns)


@pytest.mark.asyncio
async def test_unreachable_verdict_does_not_abort() -> None:
    aborts: list[tuple[str, str]] = []
    await _run_loop(
        verdicts=[_unreachable()],
        aborts=aborts,
        abort_on_bad_verdict=True,
        stop_after=1,
    )
    assert aborts == []


@pytest.mark.asyncio
async def test_probe_exception_does_not_kill_loop() -> None:
    """Probe HTTP error must not cancel the watchdog — keep retrying."""
    app_state = SimpleNamespace(ollama_probe_result=None)
    aborts: list[tuple[str, str]] = []
    calls = {"n": 0}

    async def _probe(_prefix: str, **__) -> ProbeResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient httpx error")
        if calls["n"] == 2:
            return _gpu()
        raise asyncio.CancelledError()

    task = asyncio.create_task(watchdog_loop(
        app_state,
        interval_s=0.0,
        probe_fn=_probe,
        abort_fn=lambda p, m: aborts.append((p, m)),
    ))
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        task.cancel()
        try:
            await task
        except BaseException:
            pass

    assert calls["n"] >= 2, "loop should have retried after exception"
    assert aborts == []


@pytest.mark.asyncio
async def test_cancellation_propagates() -> None:
    app_state = SimpleNamespace(ollama_probe_result=None)

    async def _probe(_prefix: str, **__) -> ProbeResult:
        return _gpu()

    task = asyncio.create_task(watchdog_loop(
        app_state, interval_s=10.0, probe_fn=_probe, abort_fn=lambda *_: None,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
