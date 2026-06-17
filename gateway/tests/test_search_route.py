"""Tests for `/v1/search` unified search route.

Vault / chat / entity legs hit a real (test-fixture) vault DB only
when configured; in this fixture they return empty. The image and
escalation legs are exercised end-to-end with stubs wired into
AppState.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_search_requires_q(client: TestClient, paired_token):
    _, token = paired_token
    r = client.get("/v1/search", headers=_auth(token))
    assert r.status_code == 422


def test_search_returns_empty_when_no_data(client: TestClient, paired_token):
    _, token = paired_token
    r = client.get("/v1/search?q=anything", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == []


def test_search_filters_by_kinds(client: TestClient, paired_token):
    _, token = paired_token
    # Wire fake recent-images that match
    @dataclass
    class _Job:
        job_id: str = "j1"
        device_id: str = "d"
        bot: str = "terry"
        prompt: str = "kraken on a beach"
        created_at: float = 1000.0
        state: str = "done"
        result_ids: list[str] = field(default_factory=lambda: ["img1"])
        error: str | None = None

    class _ImagesStore:
        def all_recent(self, *, limit=200, **_):
            return [_Job()]

    client.app.state.ai_team.recent_images = _ImagesStore()

    # Selecting only kinds=image should still match because the prompt
    # contains "kraken".
    r = client.get(
        "/v1/search?q=kraken&kinds=image", headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["kind"] == "image"
    assert body[0]["title"].startswith("kraken")
    assert body[0]["ref"]["job_id"] == "j1"

    # kinds=escalation should now return empty since no escalations are wired
    r2 = client.get(
        "/v1/search?q=kraken&kinds=escalation", headers=_auth(token),
    )
    assert r2.status_code == 200
    assert r2.json() == []


def test_search_matches_escalation_summary(client: TestClient, paired_token):
    _, token = paired_token

    @dataclass
    class _Esc:
        slug: str = "slug-1"
        path: Path = Path(".")
        title: str = "GPU stuck"
        severity: str = "high"
        reported_at: str = ""
        device_id: str = ""
        summary: str = "Render queue jammed on the kraken render"
        context: str = ""
        user_msg: str = ""
        resolved: bool = False
        body: str = ""

    class _EscStore:
        def list(self, *, include_resolved=False):
            return [_Esc()]

    client.app.state.ai_team.escalation_store = _EscStore()

    r = client.get(
        "/v1/search?q=kraken&kinds=escalation",
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["kind"] == "escalation"
    assert body[0]["title"] == "GPU stuck"
    assert body[0]["ref"]["severity"] == "high"


def test_search_invalid_kinds_falls_back_to_all(client: TestClient, paired_token):
    _, token = paired_token
    r = client.get(
        "/v1/search?q=zzz&kinds=garbage,morestuff",
        headers=_auth(token),
    )
    # An unknown kind falls back to all surfaces, all of which are
    # empty in this fixture, so we get an empty list (not a 422).
    assert r.status_code == 200
    assert r.json() == []


def test_search_rrf_score_ordering(client: TestClient, paired_token):
    """Hits across kinds should be sorted by score (then ts)."""
    _, token = paired_token

    @dataclass
    class _Job:
        job_id: str
        device_id: str = "d"
        bot: str = "terry"
        prompt: str = "kraken"
        created_at: float = 0.0
        state: str = "done"
        result_ids: list[str] = field(default_factory=list)
        error: str | None = None

    @dataclass
    class _Esc:
        slug: str
        path: Path = Path(".")
        title: str = "kraken"
        severity: str = "low"
        reported_at: str = ""
        device_id: str = ""
        summary: str = "kraken summary"
        context: str = ""
        user_msg: str = ""
        resolved: bool = False
        body: str = ""

    class _ImagesStore:
        def all_recent(self, *, limit=200, **_):
            return [
                _Job("a", created_at=2000.0),
                _Job("b", created_at=1000.0),
            ]

    class _EscStore:
        def list(self, *, include_resolved=False):
            return [_Esc("x")]

    client.app.state.ai_team.recent_images = _ImagesStore()
    client.app.state.ai_team.escalation_store = _EscStore()

    r = client.get(
        "/v1/search?q=kraken&limit=20",
        headers=_auth(token),
    )
    assert r.status_code == 200
    body = r.json()
    # All 3 hits should appear; the rank-1 image and rank-1 escalation
    # tie on score (1/61), so newest ts wins; rank-2 image (1/62) is last.
    assert len(body) == 3
    assert body[0]["score"] >= body[1]["score"] >= body[2]["score"]
    # First two have score 1/61, third has score 1/62
    assert abs(body[0]["score"] - body[1]["score"]) < 1e-9
    assert body[2]["score"] < body[1]["score"]
