"""Heartbeat-miss → orphaned-job requeue.

Pair a node, dispatch a job to it, fast-forward its last_seen past the
offline threshold, run the offline sweep, and verify the job goes back
to 'queued' with no terminal state.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.worker_pool.registry import sweep_offline_nodes


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


def test_offline_sweep_requeues_in_flight_jobs(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token)
    # Enqueue + dispatch.
    job_id = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={"kind": "k", "payload": {}, "required_caps": ["ollama"]},
    ).json()["id"]
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 200

    # Fast-forward last_seen past the offline window.
    st = client.app.state.ai_team
    import sqlite3
    db = st.config.state_dir / st.config.nodes.db_filename
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE hive_nodes SET last_seen = 0 WHERE id = ?", (node_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Run a sweep.
    n_requeued, n_failed = sweep_offline_nodes(
        registry=st.node_registry,
        dispatcher=st.dispatcher,
        offline_after_s=st.config.nodes.heartbeat_offline_seconds,
    )
    assert n_requeued == 1
    assert n_failed == 0
    job = st.dispatcher.get(job_id)
    assert job.status == "queued"
    assert job.node_id is None


def test_offline_sweep_marks_failed_at_max_attempts(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token)
    job_id = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={
            "kind": "k", "payload": {},
            "required_caps": ["ollama"], "max_attempts": 1,
        },
    ).json()["id"]
    client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    st = client.app.state.ai_team
    import sqlite3
    db = st.config.state_dir / st.config.nodes.db_filename
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE hive_nodes SET last_seen = 0 WHERE id = ?", (node_id,),
        )
        conn.commit()
    finally:
        conn.close()
    n_requeued, n_failed = sweep_offline_nodes(
        registry=st.node_registry,
        dispatcher=st.dispatcher,
        offline_after_s=st.config.nodes.heartbeat_offline_seconds,
    )
    assert n_requeued == 0
    assert n_failed == 1
    job = st.dispatcher.get(job_id)
    assert job.status == "failed"
