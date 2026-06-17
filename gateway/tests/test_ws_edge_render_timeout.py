"""Test 2 — _render_and_stream timeout.

If the image bus never delivers a result and the timeout expires,
`_render_and_stream` must emit an `image_slow` (clean bail-out) rather
than hanging the test forever.  We monkeypatch `_IMAGE_TIMEOUT_SECONDS`
to a tiny value so the test runs in milliseconds.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

import gateway.routes.chat as chat_mod
from gateway.routes.chat import _render_and_stream


# ---------------------------------------------------------------- fakes


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.query_params: dict = {}

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@dataclass
class _FakeJob:
    id: str = "job-42"
    state: str = "pending"
    result_ids: list = None
    error: str | None = None

    def __post_init__(self):
        if self.result_ids is None:
            self.result_ids = []


class _NeverDeliverQueue:
    """A queue whose get() never resolves (simulates a hung LLM/GPU)."""

    async def get(self) -> dict:
        await asyncio.sleep(3600)  # effectively infinite
        return {}  # pragma: no cover


class _FakeShim:
    def __init__(self) -> None:
        self._job = _FakeJob()

    async def enqueue(self, **_: Any) -> _FakeJob:
        return self._job

    def get(self, job_id: str) -> _FakeJob:
        return self._job


class _FakeBus:
    def __init__(self) -> None:
        self._queue = _NeverDeliverQueue()

    async def subscribe(self, _topic: str) -> _NeverDeliverQueue:
        return self._queue

    async def unsubscribe(self, _queue: Any) -> None:
        pass


class _FakeAppState:
    def __init__(self) -> None:
        self.background_tasks: set = set()
        self.image_shim = _FakeShim()
        self.event_bus = _FakeBus()
        self.pending_image_refs: dict = {}
        self.recent_images = None


# ---------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_render_and_stream_bails_out_on_timeout(monkeypatch):
    """With a tiny timeout, _render_and_stream must send image_slow and return
    instead of hanging indefinitely."""
    monkeypatch.setattr(chat_mod, "_IMAGE_TIMEOUT_SECONDS", 0.05)

    ws = _FakeWebSocket()
    state = _FakeAppState()

    # Must complete well within 2 seconds.
    await asyncio.wait_for(
        _render_and_stream(
            ws, state,  # type: ignore[arg-type]
            {"prompt": "a sunset", "count": 1},
            device_id="dev-to",
        ),
        timeout=2.0,
    )

    types = [m["type"] for m in ws.sent]
    assert "image_pending" in types, "must emit image_pending before timing out"
    assert "image_slow" in types, "must emit image_slow on timeout, not hang"
    assert "image_done" not in types


@pytest.mark.asyncio
async def test_render_and_stream_missing_pipeline_sends_error():
    """Without image_shim/event_bus configured, must emit error immediately."""
    ws = _FakeWebSocket()

    class _BareState:
        background_tasks: set = set()
        image_shim = None
        event_bus = None
        pending_image_refs: dict = {}
        recent_images = None

    await _render_and_stream(
        ws, _BareState(),  # type: ignore[arg-type]
        {"prompt": "test", "count": 1},
        device_id="dev-bare",
    )

    assert ws.sent == [{"type": "error", "message": "image pipeline not configured"}]
