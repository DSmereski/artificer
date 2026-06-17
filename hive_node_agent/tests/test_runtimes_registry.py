"""Tests for the runtime adapter contract + registry."""

from __future__ import annotations

import pytest

from hive_node_agent.runtimes import (
    RUNTIMES,
    RuntimeAdapter,
    RuntimeResult,
    get_adapter,
    register_adapter,
)


@pytest.fixture(autouse=True)
def _clear_runtimes():
    RUNTIMES.clear()
    yield
    RUNTIMES.clear()


class _DummyAdapter(RuntimeAdapter):
    name = "dummy"

    async def probe(self) -> dict:
        return {"installed": True, "version": "0.0.0"}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, payload: dict) -> RuntimeResult:
        return RuntimeResult(status="done", output={"echo": payload}, duration_ms=1)


def test_register_and_get_adapter() -> None:
    register_adapter(_DummyAdapter())
    a = get_adapter("dummy")
    assert isinstance(a, _DummyAdapter)


def test_get_unknown_adapter_raises() -> None:
    with pytest.raises(KeyError):
        get_adapter("not-a-real-adapter")


@pytest.mark.asyncio
async def test_dummy_adapter_round_trip() -> None:
    register_adapter(_DummyAdapter())
    a = get_adapter("dummy")
    result = await a.run({"x": 1})
    assert result.status == "done"
    assert result.output == {"echo": {"x": 1}}
    assert result.duration_ms == 1


def test_RUNTIMES_starts_empty_or_known() -> None:
    """Whatever's registered must be string-keyed and adapter-valued."""
    for k, v in RUNTIMES.items():
        assert isinstance(k, str)
        assert isinstance(v, RuntimeAdapter)
