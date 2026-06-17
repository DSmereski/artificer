"""Rate-limit smoke: the /v1/images bucket denies once the burst is exhausted."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gateway.rate_limit import RateLimiter


def test_rate_limited_blocks_after_burst(
    client: TestClient, paired_token: tuple[str, str], tmp_path, monkeypatch
) -> None:
    # Install a tight limiter: 2 writes max.
    rl = RateLimiter()
    rl.configure(writes_per_minute=2, images_per_hour=120)
    # Override bucket refill to effectively zero for the test window.
    rl._configs["writes"] = (2, 2)     # 2 per minute, burst 2
    client.app.state.ai_team.rate_limiter = rl

    # /v1/vault/learn requires the writes bucket. Fake the vault client.
    class _Fake:
        def __init__(self, *a, **k): pass
        async def learn(self, **kw):
            return {"ok": True, "path": f"knowledge/{kw['title']}.md", "created": True}
    monkeypatch.setattr("shared.vault_client.VaultClient", _Fake)

    _, token = paired_token
    headers = {"Authorization": f"Bearer {token}"}
    # Body must clear the vault_quality gate so the request reaches
    # the rate limiter; a 4-char body would 422 before that check.
    body_text = (
        "Rate-limit test note. This body has enough informative tokens "
        "to satisfy the vault quality gate without being a link list."
    )
    payload = {"category": "knowledge", "title": "x", "body": body_text}

    assert client.post("/v1/vault/learn", headers=headers, json={**payload, "title": "Alpha"}).status_code == 200
    assert client.post("/v1/vault/learn", headers=headers, json={**payload, "title": "Beta"}).status_code == 200
    r = client.post("/v1/vault/learn", headers=headers, json={**payload, "title": "Gamma"})
    assert r.status_code == 429
    assert "rate limit" in r.json()["detail"].lower()
