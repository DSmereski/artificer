"""Pin behavior of the gateway's helper-model prewarm step.

Without prewarm + ``keep_alive=24h``, the first user turn after gateway
boot pays a 30-90s Ollama cold-load that blows planner/summarizer
helper timeouts (gateway/hive_coordinator.py). The prewarm fires a
tiny chat to each distinct Ollama model used by planner/summarizer/
synthesizer roles so the weights are resident before the first turn.

These tests pin:
  * router=None → no-op (no orchestrator wired)
  * cloud-only model (no ollama_name) → skipped silently
  * multiple roles → distinct ollama_name dedupe
  * OllamaInvoker raises → boot not blocked
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from gateway.app import _prewarm_helper_models


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
        self.calls: list[str] = []

    def route_for(self, role: str) -> _Choice:
        self.calls.append(role)
        if role not in self._mapping:
            raise KeyError(role)
        return _Choice(model=self._mapping[role])


@pytest.fixture
def captured_invokes(monkeypatch):
    """Replace OllamaInvoker.chat with an awaitable recorder."""
    invocations: list[str] = []

    class _RecordingInvoker:
        def __init__(self, *_, **__) -> None:
            pass

        async def chat(self, *, model, system, user, params):
            invocations.append(model)
            return ("", 0, 0)

    import gateway.helpers.base as base_mod
    monkeypatch.setattr(base_mod, "OllamaInvoker", _RecordingInvoker)
    return invocations


@pytest.mark.asyncio
async def test_prewarm_noop_when_router_missing() -> None:
    await _prewarm_helper_models(router=None)


@pytest.mark.asyncio
async def test_prewarm_skips_cloud_only_models(captured_invokes) -> None:
    """Models with no ``ollama_name`` (cloud-hosted) don't need warming."""
    router = _FakeRouter({
        "planner": _Model(id="claude-opus", ollama_name=""),
        "summarizer": _Model(id="claude-haiku", ollama_name=""),
    })

    await _prewarm_helper_models(router, roles=("planner", "summarizer"))

    assert captured_invokes == []


@pytest.mark.asyncio
async def test_prewarm_dedupes_shared_model(captured_invokes) -> None:
    """If two roles route to the same model, only warm it once."""
    shared = _Model(id="planner-qwen", ollama_name="planner-qwen")
    router = _FakeRouter({
        "planner": shared,
        "summarizer": shared,
        "synthesizer": shared,
    })

    await _prewarm_helper_models(router)

    assert captured_invokes == ["planner-qwen"]


@pytest.mark.asyncio
async def test_prewarm_swallows_invoker_errors(monkeypatch) -> None:
    """If Ollama is unreachable, prewarm logs and returns without raising."""
    class _FailingInvoker:
        def __init__(self, *_, **__) -> None:
            pass

        async def chat(self, **__):
            raise ConnectionError("ollama down")

    import gateway.helpers.base as base_mod
    monkeypatch.setattr(base_mod, "OllamaInvoker", _FailingInvoker)

    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
    })

    await _prewarm_helper_models(router, roles=("planner",))


@pytest.mark.asyncio
async def test_prewarm_warms_distinct_ollama_models(captured_invokes) -> None:
    """Distinct ollama_names get distinct warmup calls."""
    router = _FakeRouter({
        "planner": _Model(id="planner-qwen", ollama_name="planner-qwen"),
        "summarizer": _Model(id="qwen3-coder", ollama_name="qwen3-coder"),
    })

    await _prewarm_helper_models(router, roles=("planner", "summarizer"))

    assert sorted(captured_invokes) == ["planner-qwen", "qwen3-coder"]
