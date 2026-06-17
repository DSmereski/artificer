"""Tests for gateway.auth + /v1/pair flow."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_pair_new_is_loopback_only(client: TestClient) -> None:
    r = client.get("/v1/pair/new")
    assert r.status_code == 200
    data = r.json()
    assert len(data["code"]) >= 8
    assert data["expires_in_seconds"] == 60


def test_pair_roundtrip_issues_token(client: TestClient) -> None:
    code = client.get("/v1/pair/new").json()["code"]
    r = client.post(
        "/v1/pair",
        json={"code": code, "name": "my-phone", "platform": "android"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"]
    assert data["name"] == "my-phone"


def test_pair_code_is_single_use(client: TestClient) -> None:
    code = client.get("/v1/pair/new").json()["code"]
    r1 = client.post("/v1/pair", json={"code": code, "name": "A", "platform": "t"})
    assert r1.status_code == 200
    r2 = client.post("/v1/pair", json={"code": code, "name": "B", "platform": "t"})
    assert r2.status_code == 401


def test_pair_rejects_bad_code(client: TestClient) -> None:
    r = client.post(
        "/v1/pair",
        json={"code": "NOTACODE", "name": "x", "platform": "t"},
    )
    assert r.status_code == 401


def test_devices_requires_auth(client: TestClient) -> None:
    r = client.get("/v1/devices")
    assert r.status_code == 401


def test_devices_with_token(client: TestClient, paired_token: tuple[str, str]) -> None:
    device_id, token = paired_token
    r = client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    devs = r.json()
    assert any(d["id"] == device_id for d in devs)


def test_revoke_device_hard_purges(client: TestClient, paired_token: tuple[str, str]) -> None:
    """DELETE /v1/devices/{id} now hard-purges the row (not soft-revoke)
    so the user's paired-device list doesn't accrue every old phone or
    smoke-test pairing forever."""
    _, token = paired_token
    # Add a second device we'll delete.
    code = client.get("/v1/pair/new").json()["code"]
    second = client.post(
        "/v1/pair",
        json={"code": code, "name": "other", "platform": "t"},
    ).json()
    r = client.delete(
        f"/v1/devices/{second['device_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204

    # The token shouldn't authenticate any more.
    r2 = client.get(
        "/v1/devices",
        headers={"Authorization": f"Bearer {second['token']}"},
    )
    assert r2.status_code == 401

    # AND the device must be GONE from the list — not just shown as
    # `revoked: true`. Was the bug the user reported.
    r3 = client.get(
        "/v1/devices", headers={"Authorization": f"Bearer {token}"},
    )
    assert r3.status_code == 200
    assert all(d["id"] != second["device_id"] for d in r3.json())


def test_devices_purge_endpoint(client: TestClient, paired_token: tuple[str, str]) -> None:
    """POST /v1/devices/purge hard-removes revoked + transient test pairings."""
    _, token = paired_token
    h = {"Authorization": f"Bearer {token}"}
    # Pair a few smoke-named devices to simulate test leakage.
    for name in ("smoke", "vault-smoke", "video-smoke", "real-phone"):
        code = client.get("/v1/pair/new").json()["code"]
        client.post(
            "/v1/pair", json={"code": code, "name": name, "platform": "t"},
        )
    r = client.post("/v1/devices/purge", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purged"] >= 3  # smoke + vault-smoke + video-smoke gone
    # real-phone (non-test prefix) survives.
    r2 = client.get("/v1/devices", headers=h).json()
    assert any(d["name"] == "real-phone" for d in r2)
    assert not any(d["name"] in ("smoke", "vault-smoke", "video-smoke") for d in r2)


def test_invalid_bearer_token(client: TestClient) -> None:
    r = client.get(
        "/v1/devices",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


def test_paired_device_has_narrow_default_audience(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """Security H-2: a phone paired via QR must NOT receive `all`-audience.

    A lost / stolen phone bearer token would otherwise be able to read
    and write content tagged for any other audience (scout, maggy, owner).
    """
    device_id, token = paired_token
    r = client.get("/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    me = next(d for d in r.json() if d["id"] == device_id)
    assert me["audience"] == ["terry", "claude-code"]
    assert "all" not in me["audience"]


def test_pair_new_rate_limited_after_burst(client: TestClient) -> None:
    """Security H-1: /v1/pair/new must rate-limit per IP.

    Tightens the bucket to a small burst, exhausts it, then asserts
    the next call returns 429. Without rate-limiting the tailnet
    surface would let an attacker mint codes endlessly.
    """
    st = client.app.state.ai_team
    # Tighten just for this test — burst=3, very low refill.
    st.rate_limiter.register("pair_attempts", per_minute=1, burst=3)
    # First three should succeed (drain the burst).
    for _ in range(3):
        r = client.get("/v1/pair/new")
        assert r.status_code == 200, r.text
    # Fourth should be throttled.
    r = client.get("/v1/pair/new")
    assert r.status_code == 429
    assert "too many" in r.json()["detail"].lower()
    # Restore generous test bucket so other tests in same session aren't
    # affected (the conftest re-registers per-fixture-instance, but be
    # defensive in case any fixture caches).
    st.rate_limiter.register("pair_attempts", per_minute=10000, burst=10000)


def test_pair_claim_rate_limited_after_burst(client: TestClient) -> None:
    """Security H-1: /v1/pair (claim) shares the bucket with /v1/pair/new.

    An attacker can't separately exhaust mint and claim — the shared
    bucket means burst exhaustion on /v1/pair/new also blocks subsequent
    /v1/pair claim attempts from the same IP.
    """
    st = client.app.state.ai_team
    st.rate_limiter.register("pair_attempts", per_minute=1, burst=2)
    # Drain the burst on /pair/new.
    for _ in range(2):
        assert client.get("/v1/pair/new").status_code == 200
    # Now /pair claim is also throttled.
    r = client.post(
        "/v1/pair",
        json={"code": "deadbeef", "name": "x", "platform": "t"},
    )
    assert r.status_code == 429
    st.rate_limiter.register("pair_attempts", per_minute=10000, burst=10000)
