"""Tests for `/v1/digest` "what's new since" route.

Each leg is exercised independently with stubs wired into AppState;
missing legs return 0 rather than 500-ing the digest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_digest_requires_since(client: TestClient, paired_token):
    _, token = paired_token
    r = client.get("/v1/digest", headers=_auth(token))
    assert r.status_code == 422


def test_digest_baseline_all_zero(client: TestClient, paired_token):
    """No stores wired → every leg degrades to 0 (never 500)."""
    _, token = paired_token
    r = client.get("/v1/digest?since=0", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["since"] == 0
    assert body["new_images"] == 0
    assert body["new_escalations"] == 0
    assert body["new_pinned_turns"] == 0
    assert body["completed_calendar_fires"] == 0


def test_digest_counts_new_images(client: TestClient, paired_token):
    _, token = paired_token

    @dataclass
    class _Job:
        job_id: str
        device_id: str = "d"
        bot: str = "hive"
        prompt: str = "p"
        created_at: float = 0.0
        state: str = "done"
        result_ids: list[str] = field(default_factory=list)
        error: str | None = None

    captured: dict = {}

    class _ImagesStore:
        def all_recent(self, *, since_ts=None, **_):
            captured["since_ts"] = since_ts
            return [_Job("a"), _Job("b"), _Job("c")]

    client.app.state.ai_team.recent_images = _ImagesStore()

    r = client.get("/v1/digest?since=1234", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["new_images"] == 3
    # Verify since was forwarded as float to the store
    assert captured["since_ts"] == 1234.0


def test_digest_counts_escalations_after_since(client: TestClient, paired_token):
    """Only escalations with reported_at >= since (ISO comparison) count."""
    _, token = paired_token

    @dataclass
    class _Esc:
        slug: str
        path: Path = Path(".")
        title: str = "t"
        severity: str = "low"
        reported_at: str = ""
        device_id: str = ""
        summary: str = ""
        context: str = ""
        user_msg: str = ""
        resolved: bool = False
        body: str = ""

    class _EscStore:
        def list(self, *, include_resolved=False):
            return [
                # 2026-01-01 — before since
                _Esc("old", reported_at="2026-01-01T00:00:00+00:00"),
                # 2026-06-15 — after since (epoch 1748044800 ≈ 2025-05)
                _Esc("recent", reported_at="2026-06-15T12:00:00+00:00"),
                # missing reported_at → never counts
                _Esc("nodate", reported_at=""),
            ]

    client.app.state.ai_team.escalation_store = _EscStore()

    # since = 2026-03-01 (epoch 1772582400)
    since = 1772582400
    r = client.get(f"/v1/digest?since={since}", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["new_escalations"] == 1


def test_digest_counts_calendar_fires(client: TestClient, paired_token):
    """Calendar jobs whose last_run_at >= since (ISO) are counted."""
    _, token = paired_token

    @dataclass
    class _Job:
        slug: str
        last_run_at: str | None = None

    class _CalStore:
        def list(self, *, limit=500):
            return [
                _Job("a", last_run_at="2026-06-01T00:00:00+00:00"),
                _Job("b", last_run_at="2026-01-01T00:00:00+00:00"),
                _Job("never", last_run_at=None),
            ]

    client.app.state.ai_team.calendar_store = _CalStore()

    since = 1772582400  # 2026-03-01
    r = client.get(f"/v1/digest?since={since}", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["completed_calendar_fires"] == 1


def test_digest_leg_failures_are_isolated(client: TestClient, paired_token):
    """A blowup in one leg returns 0 for that leg, others still tally."""
    _, token = paired_token

    class _BoomImages:
        def all_recent(self, **_):
            raise RuntimeError("boom")

    @dataclass
    class _Job:
        slug: str
        last_run_at: str | None = "2026-12-01T00:00:00+00:00"

    class _CalStore:
        def list(self, *, limit=500):
            return [_Job("a")]

    client.app.state.ai_team.recent_images = _BoomImages()
    client.app.state.ai_team.calendar_store = _CalStore()

    r = client.get("/v1/digest?since=0", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["new_images"] == 0  # boom swallowed
    assert body["completed_calendar_fires"] == 1  # other leg still counted


def test_digest_rejects_negative_since(client: TestClient, paired_token):
    _, token = paired_token
    r = client.get("/v1/digest?since=-5", headers=_auth(token))
    assert r.status_code == 422
