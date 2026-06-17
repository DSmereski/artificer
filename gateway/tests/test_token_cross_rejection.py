"""Tests for explicit cross-rejection of node vs owner tokens (F-1).

Defense-in-depth: a node token must be rejected on device-only endpoints and
vice versa, even though the two registries share no token space in production.

To verify the guard fires we manufacture a collision: we add the same raw
token string to both registries simultaneously, then assert the endpoint
returns 401 with the cross-rejection message.
"""

from __future__ import annotations

import hashlib
import secrets
import time

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pair_node(client: TestClient, owner_token: str, name: str = "rtx-rig") -> tuple[str, str]:
    """Pair a hive node and return (node_id, node_token)."""
    r = client.post("/v1/invites", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post("/v1/pair/node", json={
        "code": code, "name": name,
        "capabilities": {"agent_version": "0.1.0", "labels": [name]},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    return body["node_id"], body["token"]


def _inject_token_into_both_registries(client: TestClient, token: str) -> None:
    """Insert `token` into both device store and node registry.

    This simulates the worst-case misconfiguration that the cross-rejection
    guard is designed to catch: the same token is somehow present in both
    registries.
    """
    st = client.app.state.ai_team
    from gateway.auth import Device

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()

    # Inject into device store (in-memory, no persist needed for tests).
    fake_device = Device(
        id="d_cross_test",
        name="cross-reject-test",
        token_hash=token_hash,
        audience=("terry", "claude-code"),
        created=now,
        last_seen=now,
        revoked=False,
        user="owner",
    )
    with st.devices._lock:
        st.devices._devices["d_cross_test"] = fake_device

    # Inject into node registry (SQLite).
    conn = st.node_registry._conn
    with st.node_registry._lock:
        conn.execute(
            """INSERT OR IGNORE INTO hive_nodes
               (id, name, token_hash, created, last_seen, revoked,
                capabilities_json, agent_version, labels_json)
               VALUES (?,?,?,?,?,0,'{}','0.0.0','[]')""",
            ("n_cross_test", "cross-reject-test", token_hash, now, now),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Cross-rejection: node token on owner-only endpoint
# ---------------------------------------------------------------------------

def test_node_token_rejected_on_device_endpoint(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """F-1: when a token is present in BOTH registries, require_device rejects
    it with the cross-rejection 401 rather than accepting it as a device."""
    _, owner_token = paired_token

    collision_token = secrets.token_urlsafe(32)
    _inject_token_into_both_registries(client, collision_token)

    r = client.get("/v1/jobs", headers=_auth(collision_token))
    assert r.status_code == 401, r.text
    assert "hive node" in r.json()["detail"].lower()


def test_node_token_rejected_on_post_job(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """F-1: collision token must not be accepted for POST /v1/jobs."""
    _, owner_token = paired_token

    collision_token = secrets.token_urlsafe(32)
    _inject_token_into_both_registries(client, collision_token)

    r = client.post(
        "/v1/jobs",
        headers=_auth(collision_token),
        json={"kind": "ollama.generate", "payload": {}, "required_caps": []},
    )
    assert r.status_code == 401, r.text
    assert "hive node" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Cross-rejection: device token on node-only endpoint
# ---------------------------------------------------------------------------

def test_device_token_rejected_on_node_endpoint(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """F-1: when a token is present in BOTH registries, require_node rejects
    it with the cross-rejection 401 rather than accepting it as a node."""
    _, owner_token = paired_token

    collision_token = secrets.token_urlsafe(32)
    _inject_token_into_both_registries(client, collision_token)

    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=0",
        headers=_auth(collision_token),
    )
    assert r.status_code == 401, r.text
    assert "device" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy path sanity: each token still works on its own endpoint
# ---------------------------------------------------------------------------

def test_device_token_accepted_on_device_endpoint(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Sanity: require_device happy path is unchanged after cross-rejection patch."""
    _, owner_token = paired_token
    r = client.get("/v1/jobs", headers=_auth(owner_token))
    assert r.status_code == 200, r.text


def test_node_token_accepted_on_node_endpoint(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Sanity: require_node happy path is unchanged after cross-rejection patch."""
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)

    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=0",
        headers=_auth(node_token),
    )
    # 204 = no work queued, but auth passed
    assert r.status_code == 204, r.text
