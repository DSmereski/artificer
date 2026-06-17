"""Chat WS `done` frame carries the coordinator-assigned `turn_id`.

The Flutter client uses `turn_id` to wire per-turn actions (pin to
vault, fork-from-turn). Without it the pin/fork actions sit gated
behind a null `Message.turnId`. This test pins the contract so the
field can't silently regress.
"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient


_TURN_ID_RE = re.compile(r"^tk-[0-9a-f]{8}$")


def test_done_frame_includes_turn_id(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "hello", "user_id": 1}))
        done_msg = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "done":
                done_msg = msg
                break
            if msg["type"] == "error":
                raise AssertionError(f"server error: {msg}")

    assert done_msg is not None
    assert "turn_id" in done_msg, (
        "done frame must carry the coordinator-assigned turn_id so the "
        "client can wire per-turn actions; got: " + str(done_msg)
    )
    assert _TURN_ID_RE.match(done_msg["turn_id"]), (
        f"turn_id should look like 'tk-xxxxxxxx', got: {done_msg['turn_id']!r}"
    )


def test_done_frame_turn_id_matches_assistant_parent(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """The id on the trailing `done` frame must match the `parent` field
    the WebSocketEmitter stamps on the assistant frames during the turn,
    so clients can correlate streamed text -> final turn id."""
    _, token = paired_token
    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "hello", "user_id": 1}))
        assistant_parents: list[str] = []
        done_id: str | None = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "assistant":
                pid = msg.get("parent")
                if isinstance(pid, str) and pid:
                    assistant_parents.append(pid)
            elif msg["type"] == "done":
                done_id = msg.get("turn_id")
                break
            elif msg["type"] == "error":
                raise AssertionError(f"server error: {msg}")

    assert done_id, "done frame must include turn_id"
    assert assistant_parents, "expected assistant frames with `parent`"
    # All assistant frames in a single turn share the same parent id.
    assert set(assistant_parents) == {done_id}, (
        f"assistant parent(s) {set(assistant_parents)} should equal "
        f"done turn_id {done_id!r}"
    )


def test_done_frame_no_turn_id_when_no_hive_turn(
    client: TestClient, paired_token: tuple[str, str],
) -> None:
    """Pending-confirm 'no' bypasses the hive coordinator entirely
    (it just sends a cancel reply and clears the pending state). That
    branch never runs a turn, so the `done` frame must omit turn_id
    rather than carry a stale or empty value."""
    device_id, token = paired_token
    app_state = client.app.state.ai_team
    app_state.pending_image_confirms[device_id] = {
        "prompt": "a test image", "count": 1, "enhance": False,
    }

    with client.websocket_connect(f"/v1/chat/terry?token={token}") as ws:
        ws.send_text(json.dumps({"type": "user", "text": "no", "user_id": 1}))
        done_msg = None
        while True:
            msg = ws.receive_json()
            if msg["type"] == "done":
                done_msg = msg
                break

    assert done_msg is not None
    assert "turn_id" not in done_msg, (
        "non-hive branches (pending-confirm yes/no) must not stamp a "
        "turn_id on the done frame; got: " + str(done_msg)
    )
