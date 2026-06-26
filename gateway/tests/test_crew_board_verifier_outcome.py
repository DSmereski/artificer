"""Tests for the outcome_proven gate in verifier.verify().

The gate closes the silent false-done hole: a task that reaches `done`
because tests were green but no behavior was ever asserted (e.g. the
all-black dashboard panels shipped because smoke_cmd was absent).

Rules under test:
  1. No smoke_cmd configured → outcome_proven == False.
  2. smoke_cmd exits 0 → outcome_proven == True.
  3. smoke_cmd exits non-zero → outcome_proven == False (and ok False).
  4. The dispatcher's outcome_proven gate:
     - outcome_proven=False → NOT auto-approved, task stays in REVIEW,
       a comment explaining the reason is added, needs_review is notified.
     - outcome_proven=True → IS auto-approved to done.

Note on (4): The dispatcher's review-timeout branch checks task.updated_at
against time.monotonic(). We test it by patching _review_expired to return
True, which lets us exercise the real branch code synchronously without
sleeping 900 seconds.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.verifier import verify


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "outcome.db")


def _make_project(
    store: CrewBoardStore,
    proj_dir: Path,
    *,
    test_cmd: str | None = None,
    project_slug: str = "oproj",
) -> None:
    proj_dir.mkdir(exist_ok=True, parents=True)
    store.upsert_project(
        Project(
            slug=project_slug,
            path=str(proj_dir),
            name="Outcome Proj",
            enabled=True,
            push_allowed=False,
            test_cmd=test_cmd,
        )
    )


def _make_task(
    store: CrewBoardStore,
    project_slug: str = "oproj",
    *,
    smoke_cmd: str | None = None,
) -> object:
    task = store.create_task(
        title="outcome test task",
        body="test body",
        project_slug=project_slug,
        smoke_cmd=smoke_cmd,
    )
    return task


def _task_to_review(store: CrewBoardStore, project_slug: str, smoke_cmd: str | None = None):
    """Create a task and walk it through the state machine to STATUS_REVIEW."""
    task = store.create_task(
        title="review test task",
        body="b",
        project_slug=project_slug,
        smoke_cmd=smoke_cmd,
    )
    store.move_task(task.slug, schema.STATUS_READY, actor="owner")
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive")
    store.move_task(task.slug, schema.STATUS_QA, actor="hive")
    store.move_task(task.slug, schema.STATUS_REVIEW, actor="hive")
    store.set_review_by(task.slug, "claude-code")
    return store.get_task(task.slug)


# ---------------------------------------------------------------------------
# 1. No smoke_cmd → outcome_proven False
# ---------------------------------------------------------------------------


def test_no_smoke_cmd_outcome_not_proven(store, tmp_path):
    """A task with no smoke_cmd — tests-only green — must NOT be outcome_proven."""
    proj_dir = tmp_path / "p1"
    _make_project(store, proj_dir, test_cmd=None)
    task = _make_task(store, smoke_cmd=None)

    result = verify(store, task, run_tests=False)

    assert result.outcome_proven is False
    assert (
        "no smoke_cmd" in result.outcome_reason
        or "no outcome probe" in result.outcome_reason
    )

    # Confirm the flag is persisted in the store's verify_results.
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    vr = refreshed.verify_results or {}
    assert vr.get("outcome_proven") is False


# ---------------------------------------------------------------------------
# 2. smoke_cmd exits 0 → outcome_proven True
# ---------------------------------------------------------------------------


def test_smoke_cmd_exit_0_outcome_proven(store, tmp_path):
    """A smoke_cmd that exits 0 → outcome_proven == True."""
    proj_dir = tmp_path / "p2"
    _make_project(store, proj_dir, test_cmd=None)
    # Write a tiny script to disk so we don't need shell quoting of sys.executable.
    script = proj_dir / "_probe_pass.py"
    script.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    smoke = f"{sys.executable} _probe_pass.py"
    task = _make_task(store, smoke_cmd=smoke)

    # run_tests=True so the smoke_cmd is actually executed.
    result = verify(store, task, run_tests=True)

    assert result.outcome_proven is True, (
        f"expected outcome_proven=True, got outcome_reason={result.outcome_reason!r}, "
        f"smoke={result.tests}"
    )
    assert "smoke_cmd ran and exited 0" in result.outcome_reason

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    vr = refreshed.verify_results or {}
    assert vr.get("outcome_proven") is True
    assert vr.get("outcome_reason") == "smoke_cmd ran and exited 0"


# ---------------------------------------------------------------------------
# 3. smoke_cmd exits non-zero → outcome_proven False
# ---------------------------------------------------------------------------


def test_smoke_cmd_nonzero_exit_not_proven(store, tmp_path):
    """A smoke_cmd that exits non-zero → outcome_proven False (and ok False)."""
    proj_dir = tmp_path / "p3"
    _make_project(store, proj_dir, test_cmd=None)
    script = proj_dir / "_probe_fail.py"
    script.write_text("import sys; sys.exit(2)\n", encoding="utf-8")
    smoke = f"{sys.executable} _probe_fail.py"
    task = _make_task(store, smoke_cmd=smoke)

    result = verify(store, task, run_tests=True)

    assert result.outcome_proven is False
    # Smoke failure also makes ok=False (existing behaviour preserved).
    assert result.ok is False

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    vr = refreshed.verify_results or {}
    assert vr.get("outcome_proven") is False


# ---------------------------------------------------------------------------
# 4a. Dispatcher: not-outcome_proven task does NOT auto-approve
# ---------------------------------------------------------------------------


def test_dispatcher_no_autoapprove_without_outcome_proven(tmp_path):
    """When outcome_proven=False, the review-timeout path must NOT move the
    task to done — it must leave it in REVIEW and emit needs_review."""
    from gateway.crew_board.dispatcher import CrewDispatcher

    db = tmp_path / "disp.db"
    store = CrewBoardStore(db)
    proj_dir = tmp_path / "proj_dp"
    proj_dir.mkdir()
    store.upsert_project(
        Project(
            slug="dp", path=str(proj_dir), name="DP",
            enabled=True, push_allowed=False, test_cmd=None,
        )
    )

    # Walk the task to STATUS_REVIEW via the legal state chain.
    task = _task_to_review(store, "dp")

    # Persist verify_results with outcome_proven=False (no smoke probe).
    store.update_verify_results(task.slug, {
        "ok": True,
        "outcome_proven": False,
        "outcome_reason": "no outcome probe configured (no smoke_cmd)",
    })

    notified: list[dict] = []

    class _FakeNotifier:
        def broadcast(self, msg: dict) -> None:
            notified.append(msg)

    dispatcher = CrewDispatcher(
        store, coordinator=None, notifier=_FakeNotifier(),
    )

    # Force _review_expired to return True so we exercise the timeout branch
    # without sleeping REVIEW_TIMEOUT_S seconds.
    with patch.object(dispatcher, "_review_expired", return_value=True):
        asyncio.run(dispatcher._tick())

    # Task must still be in REVIEW — not auto-approved to done.
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_REVIEW, (
        f"expected REVIEW after no-outcome-proven gate, got {refreshed.status!r}"
    )

    # A needs_review notification must have been emitted.
    events = [n.get("event") for n in notified]
    assert "needs_review" in events, (
        f"expected needs_review notification, got: {events}"
    )

    # The comment explaining the reason must have been added.
    audit = store.audit_for(task.slug)
    comments = [a.detail for a in audit if a.action == "comment"]
    assert any(
        "no outcome probe" in c or "needs your review" in c
        for c in comments
    ), (
        f"expected an explanatory comment, got comments: {comments}"
    )


# ---------------------------------------------------------------------------
# 4b. Dispatcher: outcome_proven task DOES auto-approve
# ---------------------------------------------------------------------------


def test_dispatcher_autoapproves_when_outcome_proven(tmp_path):
    """When outcome_proven=True, the review-timeout path MUST move the
    task to done (as before, except now it's gated on the probe result)."""
    from gateway.crew_board.dispatcher import CrewDispatcher

    db = tmp_path / "disp2.db"
    store = CrewBoardStore(db)
    proj_dir = tmp_path / "proj_dp2"
    proj_dir.mkdir()
    store.upsert_project(
        Project(
            slug="dp2", path=str(proj_dir), name="DP2",
            enabled=True, push_allowed=False, test_cmd=None,
        )
    )

    task = _task_to_review(store, "dp2")

    # Persist verify_results with outcome_proven=True (smoke_cmd ran and passed).
    store.update_verify_results(task.slug, {
        "ok": True,
        "outcome_proven": True,
        "outcome_reason": "smoke_cmd ran and exited 0",
    })

    notified: list[dict] = []

    class _FakeNotifier:
        def broadcast(self, msg: dict) -> None:
            notified.append(msg)

    dispatcher = CrewDispatcher(
        store, coordinator=None, notifier=_FakeNotifier(),
    )

    with patch.object(dispatcher, "_review_expired", return_value=True):
        asyncio.run(dispatcher._tick())

    # Task must be in DONE — auto-approved because outcome was proven.
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_DONE, (
        f"expected DONE after outcome-proven auto-approve, got {refreshed.status!r}"
    )

    events = [n.get("event") for n in notified]
    assert "review_autoapproved" in events, (
        f"expected review_autoapproved notification, got: {events}"
    )
