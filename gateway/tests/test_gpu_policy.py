"""Tests for gpu_policy — the 'free the 4080' switch."""

from __future__ import annotations

import asyncio

import pytest

from gateway import gpu_policy


@pytest.fixture(autouse=True)
def _tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_policy, "_STATE_FILE", tmp_path / "gpu_mode.json")


def _set_gaming(monkeypatch, gaming: bool, gpu: int | None = 0):
    async def fake_snapshot():
        if not gaming:
            return {"game_running": None}
        return {"game_running": "Star Citizen", "game_gpu": gpu}
    monkeypatch.setattr(gpu_policy, "fetch_snapshot", fake_snapshot)


def test_default_mode_is_auto():
    assert gpu_policy.get_mode() == "auto"


def test_set_and_get_mode():
    assert gpu_policy.set_mode("force_off") == "force_off"
    assert gpu_policy.get_mode() == "force_off"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        gpu_policy.set_mode("bogus")


def test_auto_allows_4080_when_not_gaming(monkeypatch):
    gpu_policy.set_mode("auto")
    _set_gaming(monkeypatch, gaming=False)
    assert asyncio.run(gpu_policy.ai_may_use_4080()) is True
    assert asyncio.run(gpu_policy.ai_devices()) == "0,1,2"


def test_auto_evacuates_4080_when_gaming(monkeypatch):
    gpu_policy.set_mode("auto")
    _set_gaming(monkeypatch, gaming=True, gpu=0)
    assert asyncio.run(gpu_policy.ai_may_use_4080()) is False
    assert asyncio.run(gpu_policy.ai_devices()) == "1,2"


def test_auto_ignores_game_on_other_gpu(monkeypatch):
    # A game pinned to a 5060 Ti does not evict AI from the 4080.
    gpu_policy.set_mode("auto")
    _set_gaming(monkeypatch, gaming=True, gpu=1)
    assert asyncio.run(gpu_policy.ai_may_use_4080()) is True


def test_force_off_is_the_kill_switch(monkeypatch):
    gpu_policy.set_mode("force_off")
    _set_gaming(monkeypatch, gaming=False)   # even when not gaming
    assert asyncio.run(gpu_policy.ai_may_use_4080()) is False
    assert asyncio.run(gpu_policy.ai_devices()) == "1,2"


def test_force_on_overrides_gaming(monkeypatch):
    gpu_policy.set_mode("force_on")
    _set_gaming(monkeypatch, gaming=True, gpu=0)
    assert asyncio.run(gpu_policy.ai_may_use_4080()) is True


def test_status_shape(monkeypatch):
    gpu_policy.set_mode("auto")
    _set_gaming(monkeypatch, gaming=False)
    st = asyncio.run(gpu_policy.status())
    assert st == {
        "mode": "auto", "gaming": False,
        "ai_may_use_4080": True, "ai_devices": "0,1,2",
    }


def test_snapshot_failure_defaults_not_gaming(monkeypatch):
    gpu_policy.set_mode("auto")
    async def none_snap():
        return None
    monkeypatch.setattr(gpu_policy, "fetch_snapshot", none_snap)
    assert asyncio.run(gpu_policy.is_gaming()) is False
