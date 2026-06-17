"""Rate-limit tests for owner-side /v1/jobs/* endpoints (F-5).

POST /v1/jobs        → "writes" bucket
GET  /v1/jobs        → "vault_reads" bucket
GET  /v1/jobs/{id}   → "vault_reads" bucket

Node-side endpoints (GET /v1/jobs/next, POST /v1/jobs/{id}/result) must
NOT be rate-limited because the worker loop polls continuously and result
posting must never be throttled.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.rate_limit import RateLimiter


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _pair_node(client: TestClient, owner_token: str) -> tuple[str, str]:
    r = client.post("/v1/invites", headers=_auth(owner_token))
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post("/v1/pair/node", json={
        "code": code, "name": "rtx-rig",
        "capabilities": {"agent_version": "0.1.0", "labels": ["rtx-rig"]},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    return body["node_id"], body["token"]


# ---------------------------------------------------------------------------
# Rate-limit enforcement tests
# ---------------------------------------------------------------------------

def test_post_job_rate_limited_after_burst(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """F-5: POST /v1/jobs must be throttled once the writes bucket is exhausted."""
    rl = RateLimiter()
    rl.configure(writes_per_minute=2, images_per_hour=120)
    rl._configs["writes"] = (2, 2)  # burst 2, then block
    client.app.state.ai_team.rate_limiter = rl

    _, token = paired_token
    headers = _auth(token)
    job_body = {"kind": "ollama.generate", "payload": {"prompt": "hi"}, "required_caps": []}

    assert client.post("/v1/jobs", headers=headers, json=job_body).status_code == 200
    assert client.post("/v1/jobs", headers=headers, json=job_body).status_code == 200
    r = client.post("/v1/jobs", headers=headers, json=job_body)
    assert r.status_code == 429, r.text
    assert "rate limit" in r.json()["detail"].lower()


def test_list_jobs_rate_limited_after_burst(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """F-5: GET /v1/jobs must be throttled once the vault_reads bucket is exhausted."""
    rl = RateLimiter()
    rl.configure(writes_per_minute=60, images_per_hour=120)
    rl._configs["vault_reads"] = (2, 2)  # burst 2, then block
    client.app.state.ai_team.rate_limiter = rl

    _, token = paired_token
    headers = _auth(token)

    assert client.get("/v1/jobs", headers=headers).status_code == 200
    assert client.get("/v1/jobs", headers=headers).status_code == 200
    r = client.get("/v1/jobs", headers=headers)
    assert r.status_code == 429, r.text
    assert "rate limit" in r.json()["detail"].lower()


def test_get_job_detail_rate_limited_after_burst(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """F-5: GET /v1/jobs/{id} must be throttled once the vault_reads bucket is exhausted."""
    rl = RateLimiter()
    rl.configure(writes_per_minute=60, images_per_hour=120)
    rl._configs["vault_reads"] = (2, 2)
    client.app.state.ai_team.rate_limiter = rl

    _, token = paired_token
    headers = _auth(token)

    # First enqueue a job with a generous rate-limiter so we have a job id.
    generous_rl = RateLimiter()
    generous_rl.configure(writes_per_minute=1000, images_per_hour=1000)
    client.app.state.ai_team.rate_limiter = generous_rl
    r = client.post("/v1/jobs", headers=headers, json={"kind": "k", "payload": {}, "required_caps": []})
    job_id = r.json()["id"]

    # Now re-apply tight vault_reads limiter.
    client.app.state.ai_team.rate_limiter = rl

    assert client.get(f"/v1/jobs/{job_id}", headers=headers).status_code == 200
    assert client.get(f"/v1/jobs/{job_id}", headers=headers).status_code == 200
    r = client.get(f"/v1/jobs/{job_id}", headers=headers)
    assert r.status_code == 429, r.text
    assert "rate limit" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# No limiter: requests succeed when rate_limiter is None
# ---------------------------------------------------------------------------

def test_post_job_succeeds_without_rate_limiter(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Sanity: POST /v1/jobs succeeds when st.rate_limiter is None."""
    client.app.state.ai_team.rate_limiter = None
    _, token = paired_token
    r = client.post(
        "/v1/jobs",
        headers=_auth(token),
        json={"kind": "ollama.generate", "payload": {}, "required_caps": []},
    )
    assert r.status_code == 200, r.text


def test_list_jobs_succeeds_without_rate_limiter(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Sanity: GET /v1/jobs succeeds when st.rate_limiter is None."""
    client.app.state.ai_team.rate_limiter = None
    _, token = paired_token
    r = client.get("/v1/jobs", headers=_auth(token))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Node-side endpoints are NOT rate-limited
# ---------------------------------------------------------------------------

def test_node_poll_not_rate_limited(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Node-side GET /v1/jobs/next must never be throttled by the writes bucket."""
    rl = RateLimiter()
    rl.configure(writes_per_minute=1, images_per_hour=1)
    rl._configs["writes"] = (1, 1)
    rl._configs["vault_reads"] = (1, 1)
    client.app.state.ai_team.rate_limiter = rl

    _, owner_token = paired_token
    _, node_token = _pair_node(client, owner_token)

    # Even after "exhausting" write/read buckets, node poll must still pass.
    for _ in range(5):
        r = client.get(
            "/v1/jobs/next?caps=ollama&vram_mb=8000&timeout=0",
            headers=_auth(node_token),
        )
        # 204 = no work queued, but auth passed — NOT 429
        assert r.status_code == 204, f"expected 204, got {r.status_code}: {r.text}"
