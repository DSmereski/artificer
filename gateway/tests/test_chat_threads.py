"""Tests for the Phase-2 thread plumbing in `gateway/routes/chat.py`.

Pure-Python unit tests over the thread_id validator + the
`_maybe_touch_and_title_thread` helper. The HTTP routes themselves
hit the daemon over RPC so they're covered by integration tests
elsewhere.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from gateway.routes.chat import (
    _validate_thread_id,
    _new_thread_id,
    _maybe_touch_and_title_thread,
)


# ---------------------------------------------------------------- _validate_thread_id


def test_validate_thread_id_passes_default():
    assert _validate_thread_id("default") == "default"


def test_validate_thread_id_passes_url_safe_chars():
    assert _validate_thread_id("abc-123_XYZ") == "abc-123_XYZ"


def test_validate_thread_id_falls_back_on_empty():
    assert _validate_thread_id("") == "default"


def test_validate_thread_id_falls_back_on_bad_chars():
    # Slashes, dots, spaces — any of these would let a client write
    # outside the intended thread namespace.
    for bad in ("a/b", "..\\c", "has space", "with.dot", "x@y"):
        assert _validate_thread_id(bad) == "default", bad


def test_validate_thread_id_falls_back_on_too_long():
    assert _validate_thread_id("a" * 200) == "default"


def test_new_thread_id_is_url_safe_and_unique():
    a = _new_thread_id()
    b = _new_thread_id()
    assert a != b
    assert all(c.isalnum() or c in "-_" for c in a)


# ---------------------------------------------------------------- _maybe_touch_and_title_thread


@dataclass
class _Turn:
    reply: str = "hello"
    blocked: bool = False
    error: str | None = None


class _AppState:
    def __init__(self, vc: Any = None) -> None:
        self.background_tasks: set = set()
        self.vault_client = vc


class _RecordingVC:
    def __init__(self) -> None:
        self.creates: list[dict] = []
        self.touches: list[str] = []

    async def thread_create(self, *, thread_id, bot, user_id, title):
        self.creates.append({
            "thread_id": thread_id, "bot": bot,
            "user_id": user_id, "title": title,
        })
        return {"ok": True, "thread_id": thread_id, "created": True}

    async def thread_touch(self, *, thread_id):
        self.touches.append(thread_id)
        return {"ok": True}


@pytest.mark.asyncio
async def test_maybe_touch_creates_and_touches_on_real_turn():
    """A successful turn fires both thread_create (idempotent) and
    thread_touch as background tasks."""
    vc = _RecordingVC()
    state = _AppState(vc=vc)
    _maybe_touch_and_title_thread(
        state, turn=_Turn(reply="hi back"),
        bot="terry", user_id=42, text="hello world",
        thread_id="thread-abc",
    )
    # Two background tasks tracked.
    assert len(state.background_tasks) == 2
    # Drain.
    await asyncio.gather(*state.background_tasks)
    assert vc.creates == [{
        "thread_id": "thread-abc", "bot": "terry",
        "user_id": 42, "title": "hello world",
    }]
    assert vc.touches == ["thread-abc"]


@pytest.mark.asyncio
async def test_maybe_touch_skips_blocked_turn():
    vc = _RecordingVC()
    state = _AppState(vc=vc)
    _maybe_touch_and_title_thread(
        state, turn=_Turn(blocked=True),
        bot="terry", user_id=1, text="x", thread_id="t",
    )
    assert state.background_tasks == set()
    assert vc.creates == []
    assert vc.touches == []


@pytest.mark.asyncio
async def test_maybe_touch_skips_when_no_vault_client():
    state = _AppState(vc=None)
    _maybe_touch_and_title_thread(
        state, turn=_Turn(),
        bot="terry", user_id=1, text="hi", thread_id="t",
    )
    assert state.background_tasks == set()


@pytest.mark.asyncio
async def test_maybe_touch_title_is_first_50_chars_of_first_line():
    """Title heuristic: first line of user text, capped at 50 chars."""
    vc = _RecordingVC()
    state = _AppState(vc=vc)
    long_text = ("first line of the user message that is quite long indeed "
                 "and continues\nsecond line should be ignored")
    _maybe_touch_and_title_thread(
        state, turn=_Turn(),
        bot="terry", user_id=1, text=long_text, thread_id="t",
    )
    await asyncio.gather(*state.background_tasks)
    assert len(vc.creates) == 1
    title = vc.creates[0]["title"]
    assert len(title) <= 50
    assert "second line" not in title
    assert title.startswith("first line")


@pytest.mark.asyncio
async def test_maybe_touch_title_falls_back_for_empty_text():
    vc = _RecordingVC()
    state = _AppState(vc=vc)
    _maybe_touch_and_title_thread(
        state, turn=_Turn(),
        bot="terry", user_id=1, text="", thread_id="t",
    )
    await asyncio.gather(*state.background_tasks)
    assert vc.creates[0]["title"] == "(untitled)"
