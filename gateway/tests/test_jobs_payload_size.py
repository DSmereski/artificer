"""Tests for payload/output size validation on job routes (Fix 2).

POST /v1/jobs with payload > 64KiB must return 422.
POST /v1/jobs/{id}/result with output > 64KiB must return 422.
Normal-sized payloads must still be accepted.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pair_node(
    client: TestClient, owner_token: str, name: str = "node-size",
) -> tuple[str, str]:
    r = client.post("/v1/invites", headers=_auth(owner_token))
    code = r.json()["code"]
    r = client.post("/v1/pair/node", json={
        "code": code, "name": name,
        "capabilities": {"agent_version": "0.1.0", "labels": [name]},
    })
    body = r.json()
    return body["node_id"], body["token"]


# 100 KiB of string data — well over the 64 KiB cap.
_BIG_VALUE = "x" * 102_400


def test_enqueue_oversized_payload_returns_422(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={
            "kind": "ollama.generate",
            "payload": {"data": _BIG_VALUE},
            "required_caps": [],
        },
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"


def test_enqueue_normal_payload_accepted(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={
            "kind": "ollama.generate",
            "payload": {"prompt": "hello"},
            "required_caps": [],
        },
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"


def test_result_oversized_output_returns_422(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)

    # Enqueue and dispatch a job.
    r = client.post(
        "/v1/jobs",
        headers=_auth(owner_token),
        json={"kind": "k", "payload": {}, "required_caps": ["node-size"]},
    )
    job_id = r.json()["id"]
    client.get(
        "/v1/jobs/next?caps=node-size&vram_mb=20000&timeout=0",
        headers=_auth(node_token),
    )

    # Node tries to post an oversized output.
    r = client.post(
        f"/v1/jobs/{job_id}/result",
        headers=_auth(node_token),
        json={
            "status": "done",
            "output": {"data": _BIG_VALUE},
            "duration_ms": 1,
        },
    )
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
