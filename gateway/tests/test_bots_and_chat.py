"""Tests for /v1/bots list + WS /v1/chat/{bot} streaming."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_bots_list(client: TestClient, paired_token: tuple[str, str]) -> None:
    _, token = paired_token
    r = client.get("/v1/bots", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    names = [b["name"] for b in r.json()]
    # Terry is now the sole chat persona.
    assert names == ["terry"]


def test_chat_ws_requires_token(client: TestClient) -> None:
    # No token, no access.
    try:
        with client.websocket_connect("/v1/chat/terry"):
            # Connection shouldn't succeed without auth; TestClient raises on close.
            pass
    except Exception:
        return
    # If we got here without an exception, authentication didn't block — fail.
    raise AssertionError("expected WS to be closed without token")


def test_chat_ws_unknown_bot(client: TestClient, paired_token: tuple[str, str]) -> None:
    _, token = paired_token
    try:
        with client.websocket_connect(f"/v1/chat/not-a-bot?token={token}"):
            pass
    except Exception:
        return
    raise AssertionError("expected WS close on unknown bot")


def test_chat_ws_happy_path(client: TestClient, paired_token: tuple[str, str]) -> None:
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "hello", "user_id": 42}))
        chunks: list[str] = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "assistant":
                chunks.append(msg["text"])
            elif msg["type"] == "done":
                break
            elif msg["type"] == "error":
                raise AssertionError(f"server error: {msg}")
        assert "".join(chunks) == "Terry says hello"


def test_chat_ws_rejects_malformed(client: TestClient, paired_token: tuple[str, str]) -> None:
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text("not json")
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "json" in err["message"].lower()


def test_chat_ws_rejects_empty_text(client: TestClient, paired_token: tuple[str, str]) -> None:
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": ""}))
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "empty" in err["message"].lower()


def test_chat_ws_legacy_redirect_maggy(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """M1: /v1/chat/maggy redirects to Terry with a system_notice."""
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/maggy?token={token}") as ws:
        notice = ws.receive_json()
        assert notice["type"] == "system_notice"
        assert "terry" in notice["text"].lower()
        # Subsequent send should now route to Terry.
        ws.send_text(json.dumps({"type": "user", "text": "hi"}))
        chunks: list[str] = []
        while True:
            msg = ws.receive_json()
            if msg["type"] == "assistant":
                chunks.append(msg["text"])
            elif msg["type"] == "done":
                break
        assert "".join(chunks) == "Terry says hello"


def test_chat_ws_legacy_redirect_scout(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/scout?token={token}") as ws:
        notice = ws.receive_json()
        assert notice["type"] == "system_notice"
        assert "terry" in notice["text"].lower()
