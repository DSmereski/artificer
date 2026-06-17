"""Tests for /v1/jobs/* — owner enqueue + node poll/result + listings."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_post_job_requires_auth(client: TestClient) -> None:
    r = client.post("/v1/jobs", json={
        "kind": "ollama.generate",
        "payload": {"model": "qwen2.5:1.5b", "prompt": "hi"},
        "required_caps": ["ollama"],
    })
    assert r.status_code == 401


def test_post_job_returns_id_and_status_queued(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={
            "kind": "ollama.generate",
            "payload": {"model": "qwen2.5:1.5b", "prompt": "hi"},
            "required_caps": ["ollama"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"].startswith("j_")
    assert body["status"] == "queued"
    assert body["kind"] == "ollama.generate"


def test_post_job_persists_in_dispatcher(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={
            "kind": "ollama.generate",
            "payload": {"prompt": "x"},
            "required_caps": ["ollama"],
        },
    )
    job_id = r.json()["id"]
    st = client.app.state.ai_team
    job = st.dispatcher.get(job_id)
    assert job is not None
    assert job.kind == "ollama.generate"
    assert job.required_caps == ("ollama",)


def test_post_job_rejects_empty_kind(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    r = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={"kind": "", "payload": {}, "required_caps": []},
    )
    assert r.status_code == 422


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


def test_jobs_next_requires_node_auth(client: TestClient) -> None:
    r = client.get("/v1/jobs/next?caps=ollama&vram_mb=8000")
    assert r.status_code == 401


def test_jobs_next_returns_204_when_no_match(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    # Long-poll timeout shortcut: tests can pass timeout=0 to skip the wait.
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 204


def test_jobs_next_returns_matching_job(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    node_id, node_token = _pair_node(client, owner_token)
    # Owner enqueues.
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={
            "kind": "ollama.generate",
            "payload": {"prompt": "hi"},
            "required_caps": ["ollama"],
        },
    )
    job_id = r.json()["id"]
    # Node polls.
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == job_id
    assert body["kind"] == "ollama.generate"
    assert body["payload"] == {"prompt": "hi"}
    # Side effect: job is now dispatched to this node.
    st = client.app.state.ai_team
    job = st.dispatcher.get(job_id)
    assert job.status == "dispatched"
    assert job.node_id == node_id


def test_jobs_next_skips_when_caps_dont_match(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token, "comfy-only")
    client.post("/v1/jobs", headers=_auth(owner_token), json={
        "kind": "comfy.txt2img", "payload": {}, "required_caps": ["comfy"],
    })
    # This node only has 'ollama'.
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 204


def _enqueue_and_dispatch(
    client: TestClient, owner_token: str, node_token: str,
) -> str:
    """Helper: enqueue a job, have the node grab it, return job_id."""
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={
            "kind": "ollama.generate",
            "payload": {"prompt": "hi"},
            "required_caps": ["ollama"],
        },
    )
    job_id = r.json()["id"]
    r = client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    assert r.status_code == 200, r.text
    return job_id


def test_post_result_marks_done(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    job_id = _enqueue_and_dispatch(client, owner_token, node_token)
    r = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=_auth(node_token),
        json={
            "status": "done",
            "output": {"text": "hello world"},
            "duration_ms": 123,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    st = client.app.state.ai_team
    job = st.dispatcher.get(job_id)
    assert job.status == "done"
    assert job.result == {"text": "hello world"}
    assert job.duration_ms == 123


def test_post_result_marks_error(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    job_id = _enqueue_and_dispatch(client, owner_token, node_token)
    r = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=_auth(node_token),
        json={
            "status": "error",
            "error": "model not loaded",
            "duration_ms": 7,
        },
    )
    assert r.status_code == 200
    st = client.app.state.ai_team
    job = st.dispatcher.get(job_id)
    assert job.status == "error"
    assert job.error == "model not loaded"


def test_post_result_rejects_other_nodes_token(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_a_token = _pair_node(client, owner_token, "node-a")
    _, node_b_token = _pair_node(client, owner_token, "node-b")
    # Node A grabs the job.
    job_id = _enqueue_and_dispatch(client, owner_token, node_a_token)
    # Node B tries to deliver a result for it — must be rejected.
    r = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=_auth(node_b_token),
        json={"status": "done", "output": {}, "duration_ms": 1},
    )
    assert r.status_code == 403


def test_post_result_404_for_unknown_job(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    r = client.post(
        "/v1/jobs/j_missing/result",
        headers=_auth(node_token),
        json={"status": "done", "output": {}, "duration_ms": 1},
    )
    assert r.status_code == 404


def test_list_jobs_filters_by_status(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)
    # Three jobs: one done, one queued, one error.
    a = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={"kind": "k", "payload": {}, "required_caps": ["ollama"]},
    ).json()["id"]
    b = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={"kind": "k", "payload": {}, "required_caps": ["ollama"]},
    ).json()["id"]
    c = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={"kind": "k", "payload": {}, "required_caps": ["ollama"]},
    ).json()["id"]
    # Node grabs + completes a, grabs + errors b. c stays queued.
    client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    client.post(
        f"/v1/jobs/{a}/result",
        headers=_auth(node_token),
        json={"status": "done", "output": {}, "duration_ms": 1},
    )
    client.get(
        "/v1/jobs/next?caps=ollama&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )
    client.post(
        f"/v1/jobs/{b}/result",
        headers=_auth(node_token),
        json={"status": "error", "error": "x", "duration_ms": 1},
    )
    # GET /v1/jobs?status=queued returns just c.
    r = client.get("/v1/jobs?status=queued", headers=_auth(owner_token))
    assert r.status_code == 200
    ids = [j["id"] for j in r.json()]
    assert ids == [c]
    # GET /v1/jobs (all) returns all three, newest first.
    r = client.get("/v1/jobs", headers=_auth(owner_token))
    ids = [j["id"] for j in r.json()]
    assert set(ids) == {a, b, c}


def test_get_single_job(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    job_id = client.post(
        "/v1/jobs", headers=_auth(owner_token),
        json={"kind": "k", "payload": {"x": 1}, "required_caps": []},
    ).json()["id"]
    r = client.get(f"/v1/jobs/{job_id}", headers=_auth(owner_token))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id
    assert body["payload"] == {"x": 1}


def test_get_unknown_job_404(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    r = client.get("/v1/jobs/j_missing", headers=_auth(owner_token))
    assert r.status_code == 404


def test_list_requires_owner_token(client: TestClient) -> None:
    r = client.get("/v1/jobs")
    assert r.status_code == 401
