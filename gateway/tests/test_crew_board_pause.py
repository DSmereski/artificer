"""Tests for the board pause/resume gate on the CrewDispatcher.

Verifies:
  - With is_paused() True, _tick does NOT move any READY task to in_progress.
  - After set_paused(False), a subsequent _tick dispatches normally.
  - The reaper is not blocked by the pause (tested via mock call tracing).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.dispatcher import CrewDispatcher


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew_pause.db")


@pytest.fixture
def project(store: CrewBoardStore) -> str:
    """Upsert a minimal enabled project and return its slug."""
    store.upsert_project(
        Project(slug="testproj", path="/tmp/testproj", name="TestProj",
                enabled=True, push_allowed=False, test_cmd=None)
    )
    return "testproj"


def _make_dispatcher(store: CrewBoardStore) -> CrewDispatcher:
    """Build a dispatcher with a no-op coordinator and no notifier."""
    return CrewDispatcher(
        store=store,
        coordinator=MagicMock(),
        vault_path=None,
        poll_interval_s=99.0,
        notifier=None,
    )


# ------------------------------------------------------------------ helpers


def _ready_task(store: CrewBoardStore, project_slug: str):
    """Create a READY/hive-assigned task and return it."""
    t = store.create_task(
        title="pause test task",
        project_slug=project_slug,
        created_by="owner",
    )
    store.move_task(t.slug, schema.STATUS_READY, actor="owner")
    store.assign_task(t.slug, "hive", actor="owner")
    return store.get_task(t.slug)


# ------------------------------------------------------------------ tests


@pytest.mark.asyncio
async def test_paused_tick_does_not_dispatch(store, project):
    """With board paused, _tick must not move any READY task to in_progress."""
    task = _ready_task(store, project)
    dispatcher = _make_dispatcher(store)

    store.set_paused(True)

    # Patch _spawn so even if _tick wrongly calls it, no async task fires.
    with patch.object(dispatcher, "_spawn") as mock_spawn:
        with patch.object(dispatcher, "_reap_stale_in_progress") as mock_reap:
            await dispatcher._tick()

    # Reaper must still run (crash-orphan recovery keeps working).
    mock_reap.assert_called_once()
    # No task should have been spawned.
    mock_spawn.assert_not_called()
    # The task status must remain READY (not in_progress).
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_READY, (
        f"expected READY but got {refreshed.status!r}"
    )
    # inflight set must be empty.
    assert dispatcher._inflight == set()


@pytest.mark.asyncio
async def test_resumed_tick_dispatches(store, project):
    """After set_paused(False), _tick should dispatch a READY task."""
    task = _ready_task(store, project)
    dispatcher = _make_dispatcher(store)

    # Pause then immediately resume.
    store.set_paused(True)
    store.set_paused(False)

    spawned: list = []

    def _fake_spawn(coro):
        spawned.append(coro)
        # Don't actually run the coroutine — just capture it.

    with patch.object(dispatcher, "_spawn", side_effect=_fake_spawn):
        with patch.object(dispatcher, "_reap_stale_in_progress"):
            await dispatcher._tick()

    # After resume, _spawn should have been called at least once (for the
    # READY hive task).
    assert len(spawned) >= 1, "dispatcher should have spawned a task after resume"


@pytest.mark.asyncio
async def test_pause_does_not_block_reaper(store, project):
    """The reaper must be called regardless of pause state.
    Critical: crash-orphan recovery must keep working while paused.
    """
    dispatcher = _make_dispatcher(store)
    store.set_paused(True)

    reaper_calls: list[int] = []

    def _track_reap():
        reaper_calls.append(1)

    with patch.object(dispatcher, "_reap_stale_in_progress",
                      side_effect=_track_reap):
        with patch.object(dispatcher, "_spawn"):
            await dispatcher._tick()

    assert reaper_calls == [1], "reaper must be called exactly once even while paused"


@pytest.mark.asyncio
async def test_pause_transition_logged_once(store, project, caplog):
    """Pause transition should log exactly once, not on every tick."""
    import logging
    _ready_task(store, project)
    dispatcher = _make_dispatcher(store)
    store.set_paused(True)

    with patch.object(dispatcher, "_spawn"):
        with patch.object(dispatcher, "_reap_stale_in_progress"):
            with caplog.at_level(logging.INFO,
                                 logger="gateway.crew_board.dispatcher"):
                await dispatcher._tick()   # first paused tick → should log
                await dispatcher._tick()   # second paused tick → no repeat log

    pause_logs = [r for r in caplog.records if "paused" in r.message.lower()]
    assert len(pause_logs) == 1, (
        f"expected exactly 1 pause log, got {len(pause_logs)}: "
        f"{[r.message for r in pause_logs]}"
    )
