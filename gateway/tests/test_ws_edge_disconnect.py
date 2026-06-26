"""Test 1 — cancel-on-disconnect race.

When a client disconnects mid-turn, the in-progress hive turn must be
cancelled cleanly.  Pins the fix described in chat.py's comment about
the "cancelled-turn-loses-reply bug": the turn task is cancelled and
WebSocketDisconnect propagates; the fast-turn path completes without
raising.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.hive_coordinator import AssistantTurn
from gateway.routes.chat import _run_hive_turn_cancel_on_disconnect


# ---------------------------------------------------------------- fakes


class _FakeWebSocket:
    """Minimal WS double.  `receive()` returns a disconnect signal
    after `disconnect_after` calls so we can control the race."""

    def __init__(self, *, disconnect_after: int = 0) -> None:
        self.sent: list[dict] = []
        self._calls = 0
        self._disconnect_after = disconnect_after
        self.query_params: dict = {}

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive(self) -> dict:
        self._calls += 1
        if self._calls > self._disconnect_after:
            return {"type": "websocket.disconnect"}
        await asyncio.sleep(0.001)
        return {"type": "websocket.receive", "text": ""}


class _FakeAppState:
    """Minimal duck-typed AppState.

    `build_turn_context` and the hive_turn_helpers functions access several
    attributes via direct attr access (not getattr), so we must set them all
    to None or empty.  Pattern mirrors `_AppState` in test_hive_turn_helpers.py.
    """

    def __init__(self) -> None:
        self.background_tasks: set = set()
        # build_turn_context
        self.image_build_store = None
        self.skill_registry = None
        self.memory_store_hive = None
        self.helpers: dict = {}
        # persist_hive_turn_history
        self.adapters: dict = {}
        # index_hive_turn_to_chat_log
        self.vault_client = None
        # record_turn_telemetry
        self.turn_telemetry = None
        # record_turn_log
        self.turn_log_store = None
        # publish_turn_done_notifications
        self.ntfy = None
        self.event_bus = None
        # _hive_turn gates concurrent turns through this Event. Build it
        # lazily — `asyncio.Event()` needs a running loop on Py 3.10+.
        self.hive_turn_active = asyncio.Event()


class _SlowCoordinator:
    """Coordinator that takes long enough for disconnect to win."""

    def __init__(self) -> None:
        self.completed = False

    async def coordinate(self, ctx: Any, emitter: Any) -> AssistantTurn:
        await asyncio.sleep(5.0)  # longer than the test will wait
        self.completed = True
        return AssistantTurn(reply="should not arrive")


class _FastCoordinator:
    async def coordinate(self, ctx: Any, emitter: Any) -> AssistantTurn:
        return AssistantTurn(reply="fast reply")


# ---------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_disconnect_cancels_slow_turn():
    """Disconnect signal wins the race — turn task is cancelled, no orphan."""
    from fastapi import WebSocketDisconnect

    ws = _FakeWebSocket(disconnect_after=0)
    coord = _SlowCoordinator()
    state = _FakeAppState()

    with pytest.raises(WebSocketDisconnect):
        await _run_hive_turn_cancel_on_disconnect(
            ws, state,  # type: ignore[arg-type]
            coord=coord,
            user_id=1, text="hello",
            device_id="dev-disc", device_audience=None,
        )

    assert not coord.completed, "turn must be cancelled before coordinator finishes"


@pytest.mark.asyncio
async def test_fast_turn_completes_without_disconnect():
    """When the turn finishes first the watcher is torn down cleanly."""
    ws = _FakeWebSocket(disconnect_after=999)
    coord = _FastCoordinator()
    state = _FakeAppState()

    # Must not raise — turn wins the race.
    await _run_hive_turn_cancel_on_disconnect(
        ws, state,  # type: ignore[arg-type]
        coord=coord,
        user_id=1, text="quick",
        device_id="dev-fast", device_audience=None,
    )
