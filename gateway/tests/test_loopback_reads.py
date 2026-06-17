"""Tests for the loopback-exempt read-only routes.

These routes accept requests from EITHER a valid device Bearer token OR from
a loopback address (127.0.0.0/8, ::1), so the local Lively wallpaper
dashboard can populate without needing a token.

TestClient uses "testclient" as the remote host, so we patch
``gateway.deps._is_loopback`` where needed to simulate a loopback caller.
Remote-caller tests use the real (unpatchd) function.

Endpoints under test:
  GET /v1/scout/status
  GET /v1/scout/history
  GET /v1/escalations
  GET /v1/calendar/jobs/upcoming
  GET /v1/graph/* (neighbors, path, explain, god-nodes)

Also tests the new _is_loopback helper added to deps.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gateway.deps import _is_loopback as deps_loopback
from gateway.routes import scout as scout_route


# ─── Helper: pretend the client is at 127.0.0.1 ───────────────────────────────

@contextmanager
def _as_loopback():
    """Patch gateway.deps._is_loopback to return True for any host."""
    with patch("gateway.deps._is_loopback", return_value=True):
        yield


# ─── _is_loopback unit tests ──────────────────────────────────────────────────


def test_is_loopback_in_deps() -> None:
    """deps._is_loopback covers the same host set as terminal._is_loopback."""
    # Loopback addresses
    assert deps_loopback("127.0.0.1")          is True
    assert deps_loopback("::1")                is True
    assert deps_loopback("::ffff:127.0.0.1")   is True
    assert deps_loopback("127.0.0.2")          is True
    assert deps_loopback("127.255.255.255")    is True

    # Non-loopback
    assert deps_loopback("192.168.1.1")        is False
    assert deps_loopback("10.0.0.1")           is False
    assert deps_loopback("100.64.0.1")         is False  # Tailscale
    assert deps_loopback("0.0.0.0")            is False
    assert deps_loopback("")                   is False


# ─── Scout — loopback exempt ──────────────────────────────────────────────────


def _fake_snapshot() -> scout_route.ScoutStatus:
    return scout_route.ScoutStatus(
        gpus=[
            scout_route.GPUInfo(
                index=0, name="Loopback GPU", temp_c=40,
                vram_used_mb=512, vram_total_mb=8192,
                vram_used_pct=6.25, utilization_pct=5, game=None,
            )
        ],
        disks=[],
        bots=[],
    )


def test_scout_status_loopback_no_token(
    client: TestClient, monkeypatch,
) -> None:
    """GET /v1/scout/status from loopback without a token → 200."""
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    with _as_loopback():
        r = client.get("/v1/scout/status")
    assert r.status_code == 200, r.text
    assert r.json()["gpus"][0]["name"] == "Loopback GPU"


def test_scout_status_remote_no_token(
    client: TestClient, monkeypatch,
) -> None:
    """GET /v1/scout/status from a non-loopback address without a token → 401."""
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    # TestClient's host is "testclient" — not loopback, so no patch needed.
    r = client.get("/v1/scout/status")
    assert r.status_code == 401, r.text


def test_scout_status_token_still_works(
    client: TestClient,
    paired_token: tuple[str, str],
    monkeypatch,
) -> None:
    """GET /v1/scout/status with a valid Bearer token → 200 (token path unchanged)."""
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    _, token = paired_token
    r = client.get(
        "/v1/scout/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text


def test_scout_history_loopback_no_token(
    client: TestClient, paired_token: tuple[str, str], monkeypatch,
) -> None:
    """GET /v1/scout/history from loopback without a token → 200."""
    monkeypatch.setattr(scout_route, "_snapshot", _fake_snapshot)
    # Populate history via a tokened call first.
    _, token = paired_token
    with _as_loopback():
        client.get("/v1/scout/status", headers={"Authorization": f"Bearer {token}"})
        r = client.get("/v1/scout/history?limit=5")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


# ─── Escalations — loopback exempt ───────────────────────────────────────────


def test_escalations_list_loopback_no_token(client: TestClient) -> None:
    """GET /v1/escalations from loopback without a token → 200 or 503 (no store in test)."""
    with _as_loopback():
        r = client.get("/v1/escalations")
    assert r.status_code in (200, 503), r.text


def test_escalations_list_remote_no_token(client: TestClient) -> None:
    """GET /v1/escalations from a non-loopback without a token → 401."""
    r = client.get("/v1/escalations")
    assert r.status_code == 401, r.text


def test_escalations_resolve_requires_token(
    client: TestClient,
) -> None:
    """POST /v1/escalations/<slug>/resolve is a mutation — must require token even from loopback."""
    # Even from loopback, mutations must be token-gated.
    with _as_loopback():
        r = client.post("/v1/escalations/fake-slug/resolve")
    assert r.status_code == 401, r.text


# ─── Calendar upcoming — loopback exempt ─────────────────────────────────────


def test_calendar_upcoming_loopback_no_token(client: TestClient) -> None:
    """GET /v1/calendar/jobs/upcoming from loopback without a token → 200 or 503."""
    with _as_loopback():
        r = client.get("/v1/calendar/jobs/upcoming")
    assert r.status_code in (200, 503), r.text


def test_calendar_upcoming_remote_no_token(client: TestClient) -> None:
    """GET /v1/calendar/jobs/upcoming from a non-loopback without a token → 401."""
    r = client.get("/v1/calendar/jobs/upcoming")
    assert r.status_code == 401, r.text


def test_calendar_create_requires_token(client: TestClient) -> None:
    """POST /v1/calendar/jobs (a mutation) still requires a token."""
    with _as_loopback():
        r = client.post(
            "/v1/calendar/jobs",
            json={
                "title": "test job",
                "scheduled_at": "2030-01-01T00:00:00Z",
                "action_verb": "noop",
            },
        )
    assert r.status_code == 401, r.text


# ─── Graph — loopback exempt ──────────────────────────────────────────────────


def test_graph_neighbors_loopback_no_token(client: TestClient) -> None:
    """GET /v1/graph/neighbors from loopback — passes auth, may 503 (no vault DB)."""
    with _as_loopback():
        r = client.get("/v1/graph/neighbors?slug=test-entity")
    # 503 = vault DB not present in test env; important: NOT 401.
    assert r.status_code != 401, (
        f"Expected 503/404 (no DB), got {r.status_code}: {r.text}"
    )


def test_graph_god_nodes_loopback_no_token(client: TestClient) -> None:
    """GET /v1/graph/god-nodes from loopback — passes auth, may 503."""
    with _as_loopback():
        r = client.get("/v1/graph/god-nodes")
    assert r.status_code != 401, (
        f"Expected 503/404 (no DB), got {r.status_code}: {r.text}"
    )


def test_graph_neighbors_remote_no_token(client: TestClient) -> None:
    """GET /v1/graph/neighbors from a non-loopback address without a token → 401."""
    r = client.get("/v1/graph/neighbors?slug=test-entity")
    assert r.status_code == 401, r.text


def test_graph_explain_loopback_no_token(client: TestClient) -> None:
    """GET /v1/graph/explain from loopback — passes auth, may 503."""
    with _as_loopback():
        r = client.get("/v1/graph/explain?slug=test-entity")
    assert r.status_code != 401, (
        f"Expected 503/404 (no DB), got {r.status_code}: {r.text}"
    )


def test_graph_path_loopback_no_token(client: TestClient) -> None:
    """GET /v1/graph/path from loopback — passes auth, may 503."""
    with _as_loopback():
        r = client.get("/v1/graph/path?from=a&to=b")
    assert r.status_code != 401, (
        f"Expected 503/404 (no DB), got {r.status_code}: {r.text}"
    )
