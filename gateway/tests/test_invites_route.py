"""Tests for /v1/invites/* — owner-only invite lifecycle."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_post_invite_requires_auth(client: TestClient) -> None:
    r = client.post("/v1/invites")
    assert r.status_code == 401


def test_post_invite_returns_six_digit_code(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, token = paired_token
    r = client.post("/v1/invites", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    digits = body["code"].replace("-", "")
    assert digits.isdigit() and len(digits) == 6
    assert body["expires_in_seconds"] > 0


def test_list_invites_returns_active_only(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, token = paired_token
    r = client.post("/v1/invites", headers=_auth(token))
    code = r.json()["code"]
    r = client.get("/v1/invites", headers=_auth(token))
    assert r.status_code == 200
    codes = [inv["code"] for inv in r.json()]
    assert code in codes


def test_delete_invite_revokes(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, token = paired_token
    r = client.post("/v1/invites", headers=_auth(token))
    code = r.json()["code"]
    r = client.delete(f"/v1/invites/{code}", headers=_auth(token))
    assert r.status_code == 204
    r = client.get("/v1/invites", headers=_auth(token))
    assert all(inv["code"] != code for inv in r.json())
