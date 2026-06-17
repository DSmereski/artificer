"""Tests for /v1/pair/node — invite code → Bearer token."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sample_caps() -> dict:
    return {
        "agent_version": "0.1.0",
        "os": {"family": "windows", "version": "11", "build": 26200},
        "cpu": {"model": "Ryzen 9 7950X", "cores": 16, "threads": 32},
        "ram_total_gb": 64,
        "ram_free_gb": 41.2,
        "gpus": [{
            "index": 0, "name": "RTX 4090",
            "vram_total_mb": 24576, "vram_free_mb": 22100,
            "driver": "555.99", "cuda": "12.4",
        }],
        "disk_free_gb": 412.3,
        "runtimes": {},
        "labels": ["livingroom"],
    }


def _mint_invite(client: TestClient, owner_token: str) -> str:
    r = client.post("/v1/invites", headers=_auth(owner_token))
    return r.json()["code"]


def test_pair_node_with_valid_code(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    code = _mint_invite(client, owner_token)
    r = client.post("/v1/pair/node", json={
        "code": code,
        "name": "rtx-rig",
        "capabilities": _sample_caps(),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node_id"].startswith("n_")
    assert isinstance(body["token"], str) and len(body["token"]) >= 32
    assert body["name"] == "rtx-rig"


def test_pair_node_rejects_bad_code(client: TestClient) -> None:
    r = client.post("/v1/pair/node", json={
        "code": "000-000",
        "name": "rtx-rig",
        "capabilities": _sample_caps(),
    })
    assert r.status_code == 401
    assert "invalid" in r.json()["detail"].lower()


def test_pair_node_rejects_replayed_code(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    code = _mint_invite(client, owner_token)
    payload = {"code": code, "name": "rtx-rig", "capabilities": _sample_caps()}
    r = client.post("/v1/pair/node", json=payload)
    assert r.status_code == 200
    # Replay must fail.
    r = client.post("/v1/pair/node", json=payload)
    assert r.status_code == 401


def test_pair_node_persists_initial_capabilities(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    code = _mint_invite(client, owner_token)
    r = client.post("/v1/pair/node", json={
        "code": code,
        "name": "rtx-rig",
        "capabilities": _sample_caps(),
    })
    node_id = r.json()["node_id"]
    st = client.app.state.ai_team
    node = st.node_registry.get(node_id)
    assert node is not None
    assert node.agent_version == "0.1.0"
    assert "RTX 4090" in node.capabilities_json
    assert node.labels == ("livingroom",)


def test_pair_node_rejects_missing_body(client: TestClient) -> None:
    r = client.post("/v1/pair/node", json={
        "name": "rtx-rig",
        "capabilities": _sample_caps(),
    })
    assert r.status_code == 422


def test_pair_node_rate_limits_brute_force_attempts(client: TestClient) -> None:
    """An anonymous attacker hammering /v1/pair/node with bogus codes
    must hit a 429 before exhausting the 10^6 codespace. Burst is small
    on purpose — legitimate pairing is a one-shot event per node.
    """
    saw_429 = False
    for i in range(40):
        r = client.post("/v1/pair/node", json={
            "code": f"{i:03d}-{i:03d}",
            "name": "x",
            "capabilities": {},
        })
        # Either 401 (bogus code) or 429 (rate-limited). Once we see a
        # 429 the limiter is doing its job.
        assert r.status_code in (401, 429), r.text
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "rate limiter did not engage within 40 attempts"
