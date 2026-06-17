"""Gaming-aware helper concurrency cap.

The hive coordinator drops `max_concurrent_helpers` to
`gaming_concurrent_helpers` whenever scout reports a known game process
on GPU 0. These tests pin that behaviour without booting a real scout
daemon — `_gaming_on_gpu0` is monkey-patched at the coordinator module.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway import hive_coordinator as hc
from gateway.hive_coordinator import (
    HiveCoordinator,
    TurnBudget,
)


def _make_bare_coord(monkeypatch, gaming: bool) -> HiveCoordinator:
    """Build a HiveCoordinator with mocked _gaming_on_gpu0 and known caps."""

    monkeypatch.setattr(hc, "_gaming_on_gpu0", lambda: gaming)
    coord = HiveCoordinator.__new__(HiveCoordinator)
    coord.budget = TurnBudget(
        max_concurrent_helpers=5, gaming_concurrent_helpers=2,
    )
    return coord


def test_cap_resolves_to_full_when_no_game(monkeypatch) -> None:
    coord = _make_bare_coord(monkeypatch, gaming=False)
    cap, gaming = coord._resolve_helper_cap()
    assert cap == 5
    assert gaming is False


def test_cap_resolves_to_gaming_when_game_on_gpu0(monkeypatch) -> None:
    coord = _make_bare_coord(monkeypatch, gaming=True)
    cap, gaming = coord._resolve_helper_cap()
    assert cap == 2
    assert gaming is True


def test_cap_clamps_misconfigured_gaming_above_full(monkeypatch) -> None:
    """A misconfigured budget where gaming > full must clamp to full."""

    monkeypatch.setattr(hc, "_gaming_on_gpu0", lambda: True)
    coord = HiveCoordinator.__new__(HiveCoordinator)
    coord.budget = TurnBudget(
        max_concurrent_helpers=4, gaming_concurrent_helpers=99,
    )
    cap, gaming = coord._resolve_helper_cap()
    assert cap == 4
    assert gaming is True


def test_cap_falls_back_to_full_when_detect_raises(monkeypatch) -> None:
    """When the inner detector raises, the wrapper returns False -> full cap."""

    def _raise(_index: int) -> bool:
        raise RuntimeError("nvidia-smi unreachable")

    # Patch the inner symbol that `_gaming_on_gpu0` calls into. The
    # wrapper swallows the exception and reports "not gaming" so the
    # coordinator picks the full cap.
    import services.scout_daemon.gpu_monitor as gm  # noqa: WPS433
    monkeypatch.setattr(gm, "detect_game_on_gpu", _raise)

    coord = HiveCoordinator.__new__(HiveCoordinator)
    coord.budget = TurnBudget(
        max_concurrent_helpers=4, gaming_concurrent_helpers=2,
    )
    cap, gaming = coord._resolve_helper_cap()
    assert cap == 4
    assert gaming is False


def test_concurrency_endpoint_full_cap(
    client: TestClient, paired_token, monkeypatch
) -> None:
    """GET /v1/system/concurrency returns the four expected fields, no game."""
    from gateway.routes import system as system_route

    _, token = paired_token
    monkeypatch.setattr(system_route, "_gaming_on_gpu0", lambda: False)
    r = client.get(
        "/v1/system/concurrency", headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {
        "full_cap", "gaming_cap", "current_cap", "gaming_detected",
    }
    assert isinstance(body["full_cap"], int)
    assert isinstance(body["gaming_cap"], int)
    assert isinstance(body["current_cap"], int)
    assert body["gaming_detected"] is False
    assert body["current_cap"] == body["full_cap"]


def test_concurrency_endpoint_gaming_cap(
    client: TestClient, paired_token, monkeypatch,
) -> None:
    """When _gaming_on_gpu0 returns True, current_cap == gaming_cap."""
    from gateway.routes import system as system_route

    _, token = paired_token
    monkeypatch.setattr(system_route, "_gaming_on_gpu0", lambda: True)
    r = client.get(
        "/v1/system/concurrency", headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gaming_detected"] is True
    assert body["current_cap"] == body["gaming_cap"]
