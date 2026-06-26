"""Pin behavior of the chained prewarm+probe lifespan task (#438, #472).

After helper-model prewarm, gateway calls the Ollama probe to verify
planner-qwen is GPU-resident. The probe result lands on
``app_state.ollama_probe_result``; on full-CPU residency it screams
CRITICAL so the operator notices at boot rather than during the first
user turn.

These tests pin:
  * Probe runs after prewarm and stores result on AppState
  * GPU verdict logs at INFO
  * CPU verdict logs at CRITICAL with remediation hint
  * Probe exception swallowed (boot not blocked)
  * #472: CPU/mixed verdict with abort flag triggers SIGTERM
  * #472: CPU verdict with abort flag false does NOT signal
  * #472: GPU/missing/unreachable verdicts never signal even with flag
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from gateway.app import _prewarm_then_probe_hive_qwen
from gateway.ollama_probe import ProbeResult


@dataclass
class _Model:
    id: str
    ollama_name: str = ""


@dataclass
class _Choice:
    model: _Model


class _FakeRouter:
    def __init__(self, mapping: dict[str, _Model]) -> None:
        self._mapping = mapping

    def route_for(self, role: str) -> _Choice:
        if role not in self._mapping:
            raise KeyError(role)
        return _Choice(model=self._mapping[role])


@pytest.fixture
def silent_invoker(monkeypatch):
    class _RecordingInvoker:
        def __init__(self, *_, **__) -> None:
            pass

        async def chat(self, **__):
            return ("", 0, 0)

    import gateway.helpers.base as base_mod
    monkeypatch.setattr(base_mod, "OllamaInvoker", _RecordingInvoker)


@pytest.fixture
def patched_probe(monkeypatch):
    """Replace gateway.ollama_probe.check_model_on_gpu with a stub."""
    calls: list[str] = []
    result_holder: dict[str, ProbeResult] = {}

    async def _stub(model_prefix: str, **__) -> ProbeResult:
        calls.append(model_prefix)
        return result_holder["result"]

    import gateway.ollama_probe as probe_mod
    monkeypatch.setattr(probe_mod, "check_model_on_gpu", _stub)
    return calls, result_holder


@pytest.mark.asyncio
async def test_probe_runs_after_prewarm_and_stores_result(
    silent_invoker, patched_probe,
) -> None:
    calls, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=True, processor="gpu", gpu_pct=100.0,
        message="planner-qwen:latest is 100% GPU-resident",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    await _prewarm_then_probe_hive_qwen(
        router, app_state, prewarm_roles=("planner",),
    )

    assert calls == ["planner-qwen"]
    assert app_state.ollama_probe_result is holder["result"]


@pytest.mark.asyncio
async def test_cpu_verdict_logs_critical(
    silent_invoker, patched_probe, caplog,
) -> None:
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="cpu", gpu_pct=0.0,
        message="planner-qwen is 100% CPU — Ollama probably started without "
                "CUDA_VISIBLE_DEVICES=1,2 (see #437)",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    with caplog.at_level(logging.CRITICAL, logger="gateway.app"):
        await _prewarm_then_probe_hive_qwen(
            router, app_state, prewarm_roles=("planner",),
        )

    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert crit, "expected CRITICAL log on CPU residency"
    assert "start-ollama-tuned.cmd" in crit[0].getMessage()


@pytest.mark.asyncio
async def test_mixed_verdict_logs_warning(
    silent_invoker, patched_probe, caplog,
) -> None:
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="mixed", gpu_pct=40.0,
        message="40% GPU / 60% CPU",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    with caplog.at_level(logging.WARNING, logger="gateway.app"):
        await _prewarm_then_probe_hive_qwen(
            router, app_state, prewarm_roles=("planner",),
        )

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("partially offloaded" in r.getMessage() for r in warns)


@pytest.mark.asyncio
async def test_probe_exception_does_not_propagate(
    silent_invoker, monkeypatch,
) -> None:
    async def _boom(*_, **__) -> ProbeResult:
        raise RuntimeError("probe blew up")

    import gateway.ollama_probe as probe_mod
    monkeypatch.setattr(probe_mod, "check_model_on_gpu", _boom)

    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    # Should NOT raise.
    await _prewarm_then_probe_hive_qwen(
        router, app_state, prewarm_roles=("planner",),
    )
    assert app_state.ollama_probe_result is None


# ---- #472: abort-on-bad-verdict tests ---------------------------------


@pytest.fixture
def captured_sigterm(monkeypatch):
    """Capture os.kill calls so abort path can be asserted without dying."""
    import gateway.app as app_mod
    calls: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(app_mod.os, "kill", _fake_kill)
    return calls


@pytest.mark.asyncio
async def test_cpu_verdict_with_abort_flag_signals_sigterm(
    silent_invoker, patched_probe, captured_sigterm, caplog,
) -> None:
    import signal
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="cpu", gpu_pct=0.0,
        message="planner-qwen is 100% CPU",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    with caplog.at_level(logging.CRITICAL, logger="gateway.app"):
        await _prewarm_then_probe_hive_qwen(
            router, app_state,
            prewarm_roles=("planner",),
            abort_on_bad_verdict=True,
        )

    assert captured_sigterm, "expected os.kill(getpid, SIGTERM) on CPU+abort"
    pid, sig = captured_sigterm[0]
    assert sig == signal.SIGTERM
    assert pid == os.getpid()


@pytest.mark.asyncio
async def test_mixed_verdict_with_abort_flag_signals_sigterm(
    silent_invoker, patched_probe, captured_sigterm,
) -> None:
    import signal
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="mixed", gpu_pct=40.0,
        message="40% GPU / 60% CPU",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    await _prewarm_then_probe_hive_qwen(
        router, app_state,
        prewarm_roles=("planner",),
        abort_on_bad_verdict=True,
    )
    assert captured_sigterm
    assert captured_sigterm[0][1] == signal.SIGTERM


@pytest.mark.asyncio
async def test_cpu_verdict_without_abort_flag_does_not_signal(
    silent_invoker, patched_probe, captured_sigterm,
) -> None:
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="cpu", gpu_pct=0.0,
        message="planner-qwen is 100% CPU",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    await _prewarm_then_probe_hive_qwen(
        router, app_state,
        prewarm_roles=("planner",),
        abort_on_bad_verdict=False,
    )
    assert not captured_sigterm


@pytest.mark.asyncio
async def test_gpu_verdict_with_abort_flag_does_not_signal(
    silent_invoker, patched_probe, captured_sigterm,
) -> None:
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=True, processor="gpu", gpu_pct=100.0,
        message="planner-qwen 100% GPU",
        model_name="planner-qwen:latest",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    await _prewarm_then_probe_hive_qwen(
        router, app_state,
        prewarm_roles=("planner",),
        abort_on_bad_verdict=True,
    )
    assert not captured_sigterm


@pytest.mark.asyncio
async def test_unreachable_verdict_with_abort_flag_does_not_signal(
    silent_invoker, patched_probe, captured_sigterm,
) -> None:
    """Transient transport errors must not crash gateway boot.

    Unreachable/missing verdicts are flaky (Ollama still warming, network
    blip). Only definite CPU/mixed evictions trip the SIGTERM kill switch.
    """
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="unreachable", gpu_pct=0.0,
        message="ollama unreachable: ConnectError",
        model_name="",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    await _prewarm_then_probe_hive_qwen(
        router, app_state,
        prewarm_roles=("planner",),
        abort_on_bad_verdict=True,
    )
    assert not captured_sigterm


@pytest.mark.asyncio
async def test_missing_verdict_with_abort_flag_does_not_signal(
    silent_invoker, patched_probe, captured_sigterm,
) -> None:
    _, holder = patched_probe
    holder["result"] = ProbeResult(
        ok=False, processor="missing", gpu_pct=0.0,
        message="model 'planner-qwen' not loaded yet",
        model_name="",
    )
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })
    app_state = SimpleNamespace(ollama_probe_result=None)

    await _prewarm_then_probe_hive_qwen(
        router, app_state,
        prewarm_roles=("planner",),
        abort_on_bad_verdict=True,
    )
    assert not captured_sigterm
