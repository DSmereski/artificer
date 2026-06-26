"""When a turn handler raises mid-dispatch and the WS is already closed,
_ChatDispatcher.run must NOT attempt to send an error frame — that would
trigger Starlette's "Unexpected ASGI message after websocket.close" RuntimeError
seen in production logs (gateway/routes/chat_dispatcher.py:86).

Pins the fix that distinguishes WebSocketDisconnect from generic exceptions
and gates the error-frame send on the WS still being CONNECTED.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

from gateway.routes.chat_dispatcher import _ChatDispatcher


class _FakeWebSocket:
    def __init__(self, *, client_state: WebSocketState) -> None:
        self.client_state = client_state
        self.sent: list[dict] = []
        self._receive_calls = 0

    async def receive_text(self) -> str:
        self._receive_calls += 1
        if self._receive_calls > 1:
            raise WebSocketDisconnect()
        return '{"type":"user","text":"hi"}'

    async def send_json(self, payload: dict) -> None:
        if self.client_state != WebSocketState.CONNECTED:
            raise RuntimeError(
                "Unexpected ASGI message 'websocket.send', after sending"
                " 'websocket.close' or response already completed."
            )
        self.sent.append(payload)


class _FakeDevice:
    def __init__(self) -> None:
        self.id = "dev-test"
        self.user = "owner"


class _StubAppState:
    def __init__(self) -> None:
        self.pending_image_refs = None
        self.pending_image_confirms = None


@pytest.mark.asyncio
async def test_disconnect_during_handler_does_not_send_error_frame() -> None:
    """When the turn handler raises WebSocketDisconnect, dispatcher must
    propagate it without sending any frame on the closed WS."""
    ws = _FakeWebSocket(client_state=WebSocketState.DISCONNECTED)
    state = _StubAppState()

    dispatcher = _ChatDispatcher(
        websocket=ws,  # type: ignore[arg-type]
        bot="hive",
        device=_FakeDevice(),
        app_state=state,
        thread_id="default",
        user_name="owner",
    )

    async def _raise_disconnect(*, text: str, user_id: int) -> None:
        raise WebSocketDisconnect()

    dispatcher._handle_user_message = _raise_disconnect  # type: ignore[assignment]

    with pytest.raises(WebSocketDisconnect):
        await dispatcher.run()

    assert ws.sent == [], "must not send any frame on a disconnected WS"


@pytest.mark.asyncio
async def test_generic_failure_on_open_ws_sends_error_frame() -> None:
    """When the handler raises a non-disconnect error and the WS is still open,
    dispatcher should send an error frame as before."""
    ws = _FakeWebSocket(client_state=WebSocketState.CONNECTED)
    state = _StubAppState()

    dispatcher = _ChatDispatcher(
        websocket=ws,  # type: ignore[arg-type]
        bot="hive",
        device=_FakeDevice(),
        app_state=state,
        thread_id="default",
        user_name="owner",
    )

    async def _raise_value(*, text: str, user_id: int) -> None:
        raise ValueError("boom")

    dispatcher._handle_user_message = _raise_value  # type: ignore[assignment]

    with pytest.raises(WebSocketDisconnect):
        await dispatcher.run()

    assert any(
        m.get("type") == "error" and "boom" in m.get("message", "")
        for m in ws.sent
    ), f"expected error frame, got: {ws.sent}"


@pytest.mark.asyncio
async def test_generic_failure_on_closed_ws_does_not_raise() -> None:
    """When the handler raises a non-disconnect error AND the WS is closed,
    dispatcher must swallow the secondary send failure rather than escalating."""
    ws = _FakeWebSocket(client_state=WebSocketState.DISCONNECTED)
    state = _StubAppState()

    dispatcher = _ChatDispatcher(
        websocket=ws,  # type: ignore[arg-type]
        bot="hive",
        device=_FakeDevice(),
        app_state=state,
        thread_id="default",
        user_name="owner",
    )

    async def _raise_value(*, text: str, user_id: int) -> None:
        raise ValueError("boom")

    dispatcher._handle_user_message = _raise_value  # type: ignore[assignment]

    with pytest.raises(WebSocketDisconnect):
        await dispatcher.run()
    assert ws.sent == [], "closed WS must receive no frames"


# ---------------------------------------------------------------- queueing

class _MultiMsgFakeWS:
    """Hands the dispatcher N user messages back-to-back, then disconnects.

    Used to verify the new async recv/process split queues messages and
    prepends a [SYSTEM NOTE] marker on the ones that arrived while the
    prior handler was still running.
    """

    def __init__(self, *, messages: list[str], handler_delay_s: float = 0.0):
        self.client_state = WebSocketState.CONNECTED
        self.sent: list[dict] = []
        self._messages = list(messages)
        self._sent_idx = 0
        self.handler_delay_s = handler_delay_s

    async def receive_text(self) -> str:
        if self._sent_idx < len(self._messages):
            text = self._messages[self._sent_idx]
            self._sent_idx += 1
            return text
        raise WebSocketDisconnect()

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_recv_queues_messages_while_handler_busy() -> None:
    """recv_loop must accept a second user message while the handler
    is still working on the first, then deliver them in order."""
    import asyncio

    msgs = [
        '{"type":"user","text":"first message"}',
        '{"type":"user","text":"second message"}',
    ]
    ws = _MultiMsgFakeWS(messages=msgs, handler_delay_s=0.2)
    state = _StubAppState()
    dispatcher = _ChatDispatcher(
        websocket=ws,  # type: ignore[arg-type]
        bot="hive",
        device=_FakeDevice(),
        app_state=state,
        thread_id="default",
        user_name="owner",
    )

    seen: list[str] = []

    async def _slow_handler(*, text: str, user_id: int) -> str:
        seen.append(text)
        await asyncio.sleep(0.2)
        return "tid"

    dispatcher._handle_user_message = _slow_handler  # type: ignore[assignment]

    with pytest.raises(WebSocketDisconnect):
        await dispatcher.run()

    # Both messages were processed.
    assert len(seen) == 2
    assert "first message" in seen[0]
    assert "second message" in seen[1]
    # The second message arrived while the first was still being
    # handled (200ms handler delay vs ~immediate recv), so it must
    # carry the queued marker.
    assert "SYSTEM NOTE" in seen[1]
    # First message processed without a marker (idle when received).
    assert "SYSTEM NOTE" not in seen[0]


@pytest.mark.asyncio
async def test_queued_ack_sent_when_message_waits() -> None:
    """When a message arrives while busy, recv emits a `queued` ack so
    the UI can show a pending indicator."""
    import asyncio

    msgs = [
        '{"type":"user","text":"first"}',
        '{"type":"user","text":"second"}',
    ]
    ws = _MultiMsgFakeWS(messages=msgs, handler_delay_s=0.15)
    state = _StubAppState()
    dispatcher = _ChatDispatcher(
        websocket=ws,  # type: ignore[arg-type]
        bot="hive",
        device=_FakeDevice(),
        app_state=state,
        thread_id="default",
        user_name="owner",
    )

    async def _slow_handler(*, text: str, user_id: int) -> str:
        await asyncio.sleep(0.15)
        return "tid"

    dispatcher._handle_user_message = _slow_handler  # type: ignore[assignment]

    with pytest.raises(WebSocketDisconnect):
        await dispatcher.run()

    queued_acks = [m for m in ws.sent if m.get("type") == "queued"]
    assert queued_acks, f"expected a 'queued' ack frame, got: {ws.sent}"
