"""Test 3 — pending-confirm state machine.

AppState.pending_image_confirms[device_id] holds a resolved image kwargs
dict after Terry emits [CONFIRM_IMAGE].  The next user message dispatches
one of three branches:

  "yes" / "go"     -> image generates, pending cleared
  "no" / "cancel"  -> cancelled reply, pending cleared
  anything else    -> pending cleared, falls through to a new hive turn

All three branches are tested here via the real WS route + TestClient,
matching the pattern in test_bots_and_chat.py.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------- helpers


def _inject_pending(client: TestClient, device_id: str, kwargs: dict) -> None:
    """Pre-load a pending confirm for `device_id` into the live AppState."""
    app_state = client.app.state.ai_team
    app_state.pending_image_confirms[device_id] = kwargs


def _fake_kwargs() -> dict:
    return {"prompt": "a test image", "count": 1, "enhance": False}


# ---------------------------------------------------------------- tests


def test_pending_confirm_yes_triggers_image_pending(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    """Confirming with 'yes' starts a render — we get image_pending or
    an error from the missing shim, but NOT a Terry text reply."""
    device_id, token = paired_token
    _inject_pending(client, device_id, _fake_kwargs())

    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "yes", "user_id": 1}))
        msgs: list[dict] = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] in {"done", "error", "image_slow", "image_pending"}:
                # Receive one more to drain any trailing 'done'
                if m["type"] in {"image_pending", "image_slow"}:
                    continue
                break

    types = {m["type"] for m in msgs}
    # Must NOT fall through to a Terry text reply.
    assert "assistant" not in types, f"unexpected assistant message: {msgs}"
    # No pending left after any branch.
    app_state = client.app.state.ai_team
    assert device_id not in app_state.pending_image_confirms


def test_pending_confirm_no_cancels_and_clears(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    """Cancelling with 'no' emits a cancel assistant message and clears pending."""
    device_id, token = paired_token
    _inject_pending(client, device_id, _fake_kwargs())

    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "no", "user_id": 1}))
        msgs: list[dict] = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "done":
                break

    types = [m["type"] for m in msgs]
    assert "assistant" in types, "cancel branch must send an assistant message"
    cancel_texts = [m["text"] for m in msgs if m["type"] == "assistant"]
    assert any("cancel" in t.lower() for t in cancel_texts), (
        f"expected cancellation text, got: {cancel_texts}"
    )

    app_state = client.app.state.ai_team
    assert device_id not in app_state.pending_image_confirms


def test_pending_confirm_other_text_clears_and_routes_to_hive(
    client: TestClient,
    paired_token: tuple[str, str],
) -> None:
    """Any other message drops the pending and is handled as a new turn."""
    device_id, token = paired_token
    _inject_pending(client, device_id, _fake_kwargs())

    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "actually, change the colour", "user_id": 1}))
        msgs: list[dict] = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "done":
                break

    # The hive path returns Terry's fake reply — it's a real new turn.
    types = [m["type"] for m in msgs]
    assert "assistant" in types, f"expected hive turn reply: {msgs}"

    app_state = client.app.state.ai_team
    assert device_id not in app_state.pending_image_confirms
