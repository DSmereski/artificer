"""Tests for /v1/nodes/* — owner-listing + node-self-heartbeat + remove."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pair_node(
    client: TestClient, owner_token: str, name: str = "rtx-rig",
) -> tuple[str, str]:
    r = client.post("/v1/invites", headers=_auth(owner_token))
    code = r.json()["code"]
    r = client.post("/v1/pair/node", json={
        "code": code, "name": name,
        "capabilities": {"agent_version": "0.1.0", "labels": [name]},
    })
    body = r.json()
    return body["node_id"], body["token"]


def test_owner_lists_paired_nodes(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, _ = _pair_node(client, owner_token)
    r = client.get("/v1/nodes", headers=_auth(owner_token))
    assert r.status_code == 200
    ids = [n["id"] for n in r.json()]
    assert node_id in ids


def test_owner_get_node_returns_caps(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, _ = _pair_node(client, owner_token)
    r = client.get(f"/v1/nodes/{node_id}", headers=_auth(owner_token))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == node_id
    assert body["capabilities"]["agent_version"] == "0.1.0"


def test_heartbeat_updates_capabilities(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token)
    r = client.post(
        f"/v1/nodes/{node_id}/heartbeat",
        headers=_auth(node_token),
        json={"agent_version": "0.1.1", "ram_free_gb": 50.5, "labels": ["foo"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    r = client.get(f"/v1/nodes/{node_id}", headers=_auth(owner_token))
    assert r.json()["capabilities"]["agent_version"] == "0.1.1"


def test_heartbeat_with_wrong_node_id_is_forbidden(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    a_id, a_token = _pair_node(client, owner_token, "node-a")
    b_id, b_token = _pair_node(client, owner_token, "node-b")
    # Node A's token cannot heartbeat for node B's id.
    r = client.post(
        f"/v1/nodes/{b_id}/heartbeat",
        headers=_auth(a_token), json={"agent_version": "0.1.0"},
    )
    assert r.status_code == 403


def test_heartbeat_requires_node_auth(client: TestClient) -> None:
    r = client.post("/v1/nodes/n_missing/heartbeat", json={})
    assert r.status_code == 401


def test_owner_can_delete_node(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token)
    r = client.delete(f"/v1/nodes/{node_id}", headers=_auth(owner_token))
    assert r.status_code == 204
    # After delete the node's token must no longer auth.
    r = client.post(
        f"/v1/nodes/{node_id}/heartbeat",
        headers=_auth(node_token), json={},
    )
    assert r.status_code == 401


def test_heartbeat_404_when_node_purged_under_race(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """If a node is deleted between auth and registry write, the route
    must return 404 rather than letting a ValueError surface as 500.
    Simulated by purging the node directly while keeping its token alive
    (we revoke after the purge so require_node still passes).
    """
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token, "race-node")
    st = client.app.state.ai_team
    # Manually drop the row but keep a stand-in revoked=0 entry so
    # require_node still resolves the token. Easiest path: monkeypatch
    # record_heartbeat to raise ValueError directly.
    real_record = st.node_registry.record_heartbeat

    def _raise(*_args, **_kwargs):
        raise ValueError("simulated race")

    st.node_registry.record_heartbeat = _raise  # type: ignore[assignment]
    try:
        r = client.post(
            f"/v1/nodes/{node_id}/heartbeat",
            headers=_auth(node_token), json={"agent_version": "0.1.0"},
        )
    finally:
        st.node_registry.record_heartbeat = real_record  # type: ignore[assignment]
    assert r.status_code == 404
    assert "node not found" in r.text


def test_heartbeat_rejects_oversized_payload(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Capability snapshots > 32KB must be rejected with 413, never
    written to the registry. Protects against DoS via inflated heartbeats.
    """
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token, "fat-node")
    big_value = "x" * (40 * 1024)
    r = client.post(
        f"/v1/nodes/{node_id}/heartbeat",
        headers=_auth(node_token),
        json={"agent_version": "0.1.0", "junk": big_value},
    )
    assert r.status_code == 413
    assert "too large" in r.text.lower()
    # Registry must not have absorbed the oversized payload.
    r = client.get(f"/v1/nodes/{node_id}", headers=_auth(owner_token))
    caps = r.json()["capabilities"]
    assert "junk" not in caps


def test_heartbeat_response_includes_jobs_placeholder(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Phase 2 will populate `jobs` with dispatch entries. Phase 1 must
    return an empty list so the agent's response handling is forward-
    compatible without a wire-format break.
    """
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token, "p2-node")
    r = client.post(
        f"/v1/nodes/{node_id}/heartbeat",
        headers=_auth(node_token), json={"agent_version": "0.1.0"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["jobs"] == []


def test_status_field_reflects_offline_threshold(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, _ = _pair_node(client, owner_token)
    # Fast-forward the node's last_seen so it's beyond the offline window.
    st = client.app.state.ai_team
    import sqlite3
    conn = sqlite3.connect(st.config.state_dir / st.config.nodes.db_filename)
    try:
        conn.execute(
            "UPDATE hive_nodes SET last_seen = 0 WHERE id = ?", (node_id,)
        )
        conn.commit()
    finally:
        conn.close()
    # Re-open registry conn so it sees the change (registry uses its own conn,
    # so on a fresh client it should reflect after a SELECT).
    r = client.get(f"/v1/nodes/{node_id}", headers=_auth(owner_token))
    assert r.json()["status"] == "offline"
