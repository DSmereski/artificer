"""Tests that maybe_auto_title_thread respects the title_locked flag.

A thread with title_locked=1 must NOT trigger _generate_and_set_thread_title
even when all other conditions for auto-titling are met.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.hive_turn_helpers import maybe_auto_title_thread


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeMemory:
    """Minimal MemoryStore duck-type."""

    def __init__(self, turn_count: int = 3) -> None:
        self._turn_count = turn_count

    def get(self, user_id: int, thread_id: str):
        m = MagicMock()
        m.turn_count = self._turn_count
        return m


class _FakeSummarizer:
    async def invoke(self, task):
        result = MagicMock()
        result.error = None
        result.output = {"summary": "Generated Title"}
        return result


class _AppState:
    def __init__(self, **overrides):
        self.background_tasks: set = set()
        self.memory_store_terry = None
        self.vault_client = None
        self.helpers: dict = {}
        self.adapters: dict = {}
        self.image_build_store = None
        self.skill_registry = None
        self.turn_telemetry = None
        self.turn_log_store = None
        self.event_bus = None
        self.ntfy = None
        for k, v in overrides.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Test: locked thread is not auto-titled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locked_thread_skips_auto_title() -> None:
    """When title_locked=1 (truthy), maybe_auto_title_thread must NOT
    schedule _generate_and_set_thread_title."""
    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = {
        "id": "t-locked",
        "bot": "terry",
        "user_id": 1,
        "title": "Manually Set Title",
        "title_locked": 1,
        "pinned": 0,
    }

    summarizer = _FakeSummarizer()
    state = _AppState(
        memory_store_terry=_FakeMemory(turn_count=3),
        vault_client=fake_vc,
        helpers={"summarizer": summarizer},
    )

    maybe_auto_title_thread(
        state,
        bot="terry",
        user_id=1,
        text="Hello there",
        thread_id="t-locked",
    )

    # No background tasks should have been scheduled.
    assert len(state.background_tasks) == 0, (
        "Expected no background tasks for a title_locked thread"
    )

    # Drain just in case something was queued despite the guard.
    if state.background_tasks:
        await asyncio.gather(*state.background_tasks)

    # thread_set_title must not have been called.
    fake_vc.thread_set_title.assert_not_called()


@pytest.mark.asyncio
async def test_unlocked_thread_schedules_auto_title() -> None:
    """When title_locked=0 (falsy), the normal path should schedule the
    background title task (regression guard — the guard must not block
    normal operation)."""
    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = {
        "id": "t-unlocked",
        "bot": "terry",
        "user_id": 1,
        "title": "Old Heuristic Title",
        "title_locked": 0,
        "pinned": 0,
    }
    fake_vc.thread_set_title = AsyncMock(return_value={"ok": True})

    summarizer = _FakeSummarizer()
    state = _AppState(
        memory_store_terry=_FakeMemory(turn_count=3),
        vault_client=fake_vc,
        helpers={"summarizer": summarizer},
    )

    maybe_auto_title_thread(
        state,
        bot="terry",
        user_id=1,
        text="Hello there",
        thread_id="t-unlocked",
    )

    # At least one background task should have been scheduled.
    assert len(state.background_tasks) >= 1, (
        "Expected a background task for an unlocked thread"
    )

    # Drain to let the task complete.
    await asyncio.gather(*state.background_tasks)

    # thread_set_title should have been called with the generated title.
    fake_vc.thread_set_title.assert_awaited_once()


@pytest.mark.asyncio
async def test_locked_bool_true_also_skips() -> None:
    """title_locked=True (Python bool) is also truthy — must skip."""
    fake_vc = MagicMock()
    fake_vc.get_thread.return_value = {
        "id": "t-bool-locked",
        "title_locked": True,
    }

    state = _AppState(
        memory_store_terry=_FakeMemory(turn_count=3),
        vault_client=fake_vc,
        helpers={"summarizer": _FakeSummarizer()},
    )

    maybe_auto_title_thread(
        state,
        bot="terry",
        user_id=1,
        text="hi",
        thread_id="t-bool-locked",
    )
    assert len(state.background_tasks) == 0
