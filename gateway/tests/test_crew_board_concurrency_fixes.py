"""Tests for concurrency/correctness bug fixes (C1, C2, H1/H2, M5, M2).

C1: verify/git/worktree calls are offloaded to asyncio.to_thread in
    dispatcher — covered indirectly by the existing verified-green-build
    test (test_crew_board_qa.py) which still passes after the refactor.
    This file adds targeted regression tests for the other four fixes.

C2: store.py WAL pragma is active.
H1/H2: task moved out of ready before claim → _run_task bails cleanly,
       slug NOT left in _inflight, lane NOT decremented.
M5: reaper skips slugs that are in _inflight.
M2: done_slugs() includes archived tasks.
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
    return CrewBoardStore(tmp_path / "concurrency_test.db")


@pytest.fixture
def project(store: CrewBoardStore) -> str:
    """Upsert a minimal enabled project and return its slug."""
    store.upsert_project(
        Project(
            slug="testproj",
            path="/tmp/testproj",
            name="TestProj",
            enabled=True,
            push_allowed=False,
            test_cmd=None,
        )
    )
    return "testproj"


def _make_dispatcher(store: CrewBoardStore) -> CrewDispatcher:
    return CrewDispatcher(
        store=store,
        coordinator=MagicMock(),
        vault_path=None,
        poll_interval_s=99.0,
        notifier=None,
    )


def _ready_hive_task(store: CrewBoardStore, project_slug: str):
    """Create a READY/hive-assigned task and return it."""
    t = store.create_task(
        title="concurrency test task",
        project_slug=project_slug,
        created_by="owner",
    )
    store.move_task(t.slug, schema.STATUS_READY, actor="owner")
    store.assign_task(t.slug, "hive", actor="owner")
    return store.get_task(t.slug)


# ------------------------------------------------------------------ C2: WAL pragma


def test_store_wal_journal_mode(tmp_path):
    """C2: WAL journal mode must be active immediately after __init__."""
    s = CrewBoardStore(tmp_path / "wal_test.db")
    row = s._conn.execute("PRAGMA journal_mode").fetchone()
    assert row is not None
    mode = row[0].lower()
    assert mode == "wal", (
        f"expected WAL journal mode, got {mode!r}"
    )
    s.close()


def test_store_lock_is_rlock(tmp_path):
    """C2: the store lock must be a threading.RLock (reentrant) so that
    public methods calling internal helpers (_audit, _next_task_slug)
    from within a held lock don't deadlock."""
    import threading
    s = CrewBoardStore(tmp_path / "lock_test.db")
    # RLock and Lock share no public API to distinguish them cleanly;
    # probe by acquiring it twice from the same thread (deadlocks with
    # plain Lock, succeeds with RLock).
    acquired = s._lock.acquire(timeout=1)
    assert acquired, "could not acquire lock"
    try:
        reentrant = s._lock.acquire(timeout=1)
        assert reentrant, "lock is not reentrant (plain Lock instead of RLock)"
        s._lock.release()
    finally:
        s._lock.release()
    s.close()


# ------------------------------------------------------------------ M2: done_slugs includes archived


def test_done_slugs_includes_archived(store, project):
    """M2: an archived upstream must satisfy depends_on so downstream
    tasks are never permanently blocked."""
    a = store.create_task(title="upstream", project_slug=project,
                          created_by="owner")
    b = store.create_task(title="downstream", project_slug=project,
                          created_by="owner", depends_on=[a.slug])

    # Drive a -> done -> archived.
    for s in (schema.STATUS_READY, schema.STATUS_IN_PROGRESS,
              schema.STATUS_REVIEW, schema.STATUS_DONE):
        store.move_task(a.slug, s)
    store.move_task(a.slug, schema.STATUS_ARCHIVED)

    slugs = store.done_slugs()
    assert a.slug in slugs, (
        f"archived slug {a.slug!r} must appear in done_slugs() "
        f"(got {slugs!r})"
    )
    # b is still backlog — not in done slugs.
    assert b.slug not in slugs


def test_done_slugs_includes_done(store, project):
    """M2 baseline: plain done tasks must still appear in done_slugs()."""
    a = store.create_task(title="x", project_slug=project, created_by="owner")
    for s in (schema.STATUS_READY, schema.STATUS_IN_PROGRESS,
              schema.STATUS_REVIEW, schema.STATUS_DONE):
        store.move_task(a.slug, s)

    slugs = store.done_slugs()
    assert a.slug in slugs


# ------------------------------------------------------------------ H1/H2: claim-race safety


@pytest.mark.asyncio
async def test_claim_race_task_not_ready_bails_cleanly(store, project):
    """H1/H2: if the task is moved out of ready between _tick's check and
    _run_task's claim, the coroutine must bail without leaving the slug
    in _inflight."""
    task = _ready_hive_task(store, project)

    # Simulate a race: move the task to in_progress BEFORE _run_task runs
    # (as if the reaper or a route handler grabbed it first).
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="system")

    dispatcher = _make_dispatcher(store)
    # Pre-populate _inflight as _tick would have done.
    dispatcher._inflight.add(task.slug)

    await dispatcher._run_task(task.slug)

    # The slug must be removed from _inflight (no leak).
    assert task.slug not in dispatcher._inflight, (
        f"slug {task.slug!r} was left in _inflight after a pre-claim race"
    )
    # Task must remain in_progress (we did NOT move it anywhere else).
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_IN_PROGRESS, (
        f"task status should be unchanged (in_progress), got {refreshed.status!r}"
    )


@pytest.mark.asyncio
async def test_claim_race_ValueError_bails_cleanly(store, project):
    """H1/H2: if move_task raises ValueError during the claim (another
    actor moved the task between the status check and the move), the
    coroutine bails without leaking the inflight entry."""
    task = _ready_hive_task(store, project)
    dispatcher = _make_dispatcher(store)
    dispatcher._inflight.add(task.slug)

    # Patch move_task to raise ValueError on the first call (the claim).
    original_move = store.move_task
    calls = []

    def _raiser(slug, to_status, **kw):
        if to_status == schema.STATUS_IN_PROGRESS and slug == task.slug:
            calls.append(1)
            raise ValueError("simulated concurrent state change")
        return original_move(slug, to_status, **kw)

    with patch.object(store, "move_task", side_effect=_raiser):
        await dispatcher._run_task(task.slug)

    # move_task was called (and raised).
    assert calls, "move_task was not called at all"
    # Slug must be removed from _inflight.
    assert task.slug not in dispatcher._inflight, (
        "slug leaked in _inflight after ValueError on claim"
    )


@pytest.mark.asyncio
async def test_claim_race_lane_not_decremented_on_bail(store):
    """H1/H2 (parallel): when _run_task bails before the claim succeeds
    the lane counter must not be decremented (it was never incremented)."""
    store.upsert_project(
        Project(
            slug="par", path="/tmp/par", name="Par",
            enabled=True, parallel=True,
        )
    )
    t = store.create_task(title="par task", project_slug="par",
                          created_by="owner")
    store.move_task(t.slug, schema.STATUS_READY)
    store.assign_task(t.slug, "hive")
    # Move out of ready to trigger the early bail.
    store.move_task(t.slug, schema.STATUS_IN_PROGRESS, actor="system")

    dispatcher = _make_dispatcher(store)
    dispatcher._inflight.add(t.slug)
    # Lane count starts at 0.
    assert dispatcher._lane_count.get("hive", 0) == 0

    await dispatcher._run_task(t.slug)

    # Lane count must still be 0 (never incremented, never decremented).
    assert dispatcher._lane_count.get("hive", 0) == 0, (
        "lane count was decremented below 0 after an early bail"
    )


# ------------------------------------------------------------------ M5: reaper skips _inflight


@pytest.mark.asyncio
async def test_reaper_skips_inflight_slugs(store, project):
    """M5: a slug in _inflight must be skipped by the reaper even if the
    task is already in_progress (claimed but _running.add not yet done)."""
    task = _ready_hive_task(store, project)
    # Drive the task to in_progress (simulating dispatcher's claim).
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive")

    dispatcher = _make_dispatcher(store)
    # Slug is in _inflight but NOT in _running (the window between claim
    # and self._running.add in _run_task).
    dispatcher._inflight.add(task.slug)
    assert task.slug not in dispatcher._running

    # Force the heartbeat to be stale so the reaper would normally act.
    store._conn.execute(
        "UPDATE crew_tasks SET heartbeat_at = '2000-01-01 00:00:00' "
        "WHERE slug = ?",
        (task.slug,),
    )
    store._conn.commit()

    dispatcher._reap_stale_in_progress()

    # Task must still be in_progress — reaper must NOT have reaped it.
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_IN_PROGRESS, (
        f"reaper incorrectly reaped an _inflight task; "
        f"status={refreshed.status!r}"
    )


@pytest.mark.asyncio
async def test_reaper_skips_running_slugs(store, project):
    """M5 baseline: slugs in _running must still be protected (pre-existing
    behaviour must not be broken by the _inflight guard)."""
    task = _ready_hive_task(store, project)
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive")

    dispatcher = _make_dispatcher(store)
    dispatcher._running.add(task.slug)

    # Make heartbeat stale.
    store._conn.execute(
        "UPDATE crew_tasks SET heartbeat_at = '2000-01-01 00:00:00' "
        "WHERE slug = ?",
        (task.slug,),
    )
    store._conn.commit()

    dispatcher._reap_stale_in_progress()

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_IN_PROGRESS, (
        "reaper reaped a _running task — existing guard broken"
    )


@pytest.mark.asyncio
async def test_reaper_does_reap_stale_orphan(store, project):
    """M5 sanity: tasks that are truly stale (neither _running nor
    _inflight) with an old heartbeat must still be reaped."""
    task = _ready_hive_task(store, project)
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive")

    dispatcher = _make_dispatcher(store)
    # Not in _running, not in _inflight — genuine orphan.

    # Make heartbeat stale.
    store._conn.execute(
        "UPDATE crew_tasks SET heartbeat_at = '2000-01-01 00:00:00' "
        "WHERE slug = ?",
        (task.slug,),
    )
    store._conn.commit()

    dispatcher._reap_stale_in_progress()

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_READY, (
        f"reaper should have bounced the orphan to ready; "
        f"got {refreshed.status!r}"
    )
