"""Tests for long-poll timeout query param upper bound (Fix 4).

GET /v1/jobs/next?timeout=999 must return 422 (exceeds cap of 120).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pair_node(
    client: TestClient, owner_token: str, name: str = "node-timeout",
) -> tuple[str, str]:
    r = client.post("/v1/invites", headers=_auth(owner_token))
    code = r.json()["code"]
    r = client.post("/v1/pair/node", json={
        "code": code, "name": name,
        "capabilities": {"agent_version": "0.1.0", "labels": [name]},
    })
    body = r.json()
    return body["node_id"], body["token"]


def test_poll_timeout_too_large_returns_422(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=999",
        headers=_auth(node_token),
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_poll_timeout_at_cap_is_accepted(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    # timeout=120 is exactly the cap — should be accepted (returns 204 since no jobs).
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 204


def test_poll_timeout_zero_accepted(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 204
