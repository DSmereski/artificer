"""Tests for the QA swimlane in the crew board pipeline.

Covers:
  (a) schema — STATUS_QA in ALL_STATUSES + legal/illegal transitions
  (b) dispatcher moves verified-green build to qa (not review)
  (c) qa pass → review with qa_passed notify
  (d) qa fail → ready with qa_failed notify + comment
  (e) qa timeout → review with qa_timeout notify

All dispatcher tests monkeypatch run_claude_qa and the verifier so no
real subprocess is spawned. Fixture pattern mirrors test_crew_board_pause.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.crew_board import schema
from gateway.crew_board.claude_runner import QaVerdict
from gateway.crew_board.dispatcher import CrewDispatcher, QA_TIMEOUT_S
from gateway.crew_board.store import CrewBoardStore, Project


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew_qa.db")


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
            test_cmd="python -m pytest -q",
        )
    )
    return "testproj"


def _make_dispatcher(store: CrewBoardStore) -> CrewDispatcher:
    """Dispatcher with no-op coordinator, no notifier."""
    return CrewDispatcher(
        store=store,
        coordinator=MagicMock(),
        vault_path=None,
        poll_interval_s=99.0,
        notifier=None,
    )


def _qa_task(store: CrewBoardStore, project_slug: str):
    """Create a task and drive it straight to STATUS_QA."""
    t = store.create_task(
        title="qa test task",
        project_slug=project_slug,
        created_by="owner",
    )
    store.move_task(t.slug, schema.STATUS_READY, actor="owner")
    store.move_task(t.slug, schema.STATUS_IN_PROGRESS, actor="hive")
    store.move_task(t.slug, schema.STATUS_QA, actor="hive")
    return store.get_task(t.slug)


# ------------------------------------------------------------------ (a) schema


def test_qa_in_all_statuses():
    assert schema.STATUS_QA in schema.ALL_STATUSES


def test_qa_order_between_in_progress_and_review():
    order = list(schema.ALL_STATUSES)
    assert order.index(schema.STATUS_IN_PROGRESS) < order.index(schema.STATUS_QA)
    assert order.index(schema.STATUS_QA) < order.index(schema.STATUS_REVIEW)


def test_in_progress_to_qa_is_legal(store, project):
    t = store.create_task(title="x", project_slug=project, created_by="owner")
    store.move_task(t.slug, schema.STATUS_READY)
    store.move_task(t.slug, schema.STATUS_IN_PROGRESS)
    t = store.move_task(t.slug, schema.STATUS_QA)
    assert t.status == schema.STATUS_QA


def test_qa_to_review_is_legal(store, project):
    task = _qa_task(store, project)
    t = store.move_task(task.slug, schema.STATUS_REVIEW)
    assert t.status == schema.STATUS_REVIEW


def test_qa_to_ready_is_legal(store, project):
    task = _qa_task(store, project)
    t = store.move_task(task.slug, schema.STATUS_READY)
    assert t.status == schema.STATUS_READY


def test_qa_to_done_is_illegal(store, project):
    """QA must not skip review — done is not a legal qa transition."""
    task = _qa_task(store, project)
    with pytest.raises(ValueError):
        store.move_task(task.slug, schema.STATUS_DONE)


def test_ready_to_qa_is_illegal(store, project):
    """QA is only reachable from in_progress, not directly from ready."""
    t = store.create_task(title="x", project_slug=project, created_by="owner")
    store.move_task(t.slug, schema.STATUS_READY)
    with pytest.raises(ValueError):
        store.move_task(t.slug, schema.STATUS_QA)


def test_in_progress_to_review_still_legal(store, project):
    """max-attempts park path (in_progress→review) must remain valid."""
    t = store.create_task(title="x", project_slug=project, created_by="owner")
    store.move_task(t.slug, schema.STATUS_READY)
    store.move_task(t.slug, schema.STATUS_IN_PROGRESS)
    t = store.move_task(t.slug, schema.STATUS_REVIEW)
    assert t.status == schema.STATUS_REVIEW


# ------------------------------------------------------------------ (b) build success → qa


@pytest.mark.asyncio
async def test_verified_green_build_moves_to_qa_not_review(store, project):
    """After a successful build+verify, the dispatcher must move the task
    to STATUS_QA, not STATUS_REVIEW."""
    t = store.create_task(
        title="impl feature", project_slug=project, created_by="owner",
    )
    store.move_task(t.slug, schema.STATUS_READY)
    store.assign_task(t.slug, "hive")
    dispatcher = _make_dispatcher(store)

    # A fake verifier result that always passes.
    from gateway.crew_board.verifier import VerifyResult

    fake_verdict = VerifyResult(ok=True, reason="all checks green")

    # Fake hive runner result: success.
    from gateway.crew_board.hive_agent_loop import HiveLoopResult

    fake_hive = HiveLoopResult(ok=True, turns=1, reason="done", summary="wrote feature")

    with (
        patch(
            "gateway.crew_board.dispatcher.run_hive_agent_loop",
            new=AsyncMock(return_value=fake_hive),
        ),
        patch(
            "gateway.crew_board.dispatcher.verify",
            return_value=fake_verdict,
        ),
        patch.object(dispatcher, "_git_head", return_value=None),
        patch.object(dispatcher, "_git_commit_all"),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()
        # Let the spawned coroutines finish.
        await asyncio.gather(*list(dispatcher._bg_tasks), return_exceptions=True)

    refreshed = store.get_task(t.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_QA, (
        f"expected STATUS_QA after verified build, got {refreshed.status!r}"
    )


# ------------------------------------------------------------------ (c) qa pass → review


@pytest.mark.asyncio
async def test_qa_pass_moves_to_review_and_notifies(store, project):
    """A passing QA verdict must move the task to review and fire qa_passed."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    notified: list[str] = []
    dispatcher._notify = lambda event, slug: notified.append(event)  # type: ignore[method-assign]

    pass_verdict = QaVerdict(
        passed=True, reason="all tests green", tests_added=["tests/test_feat.py"],
    )

    with (
        patch(
            "gateway.crew_board.dispatcher.run_claude_qa",
            new=AsyncMock(return_value=pass_verdict),
        ),
        patch.object(dispatcher, "_git_commit_all"),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()
        await asyncio.gather(*list(dispatcher._bg_tasks), return_exceptions=True)

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_REVIEW, (
        f"expected STATUS_REVIEW after qa pass, got {refreshed.status!r}"
    )
    assert "qa_passed" in notified


@pytest.mark.asyncio
async def test_qa_pass_commits_tests(store, project):
    """On QA pass the dispatcher must call _git_commit_all to persist tests."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    pass_verdict = QaVerdict(
        passed=True, reason="green", tests_added=["tests/test_feat.py"],
    )
    committed: list[tuple] = []

    def _fake_commit(path, message):
        committed.append((path, message))

    with (
        patch(
            "gateway.crew_board.dispatcher.run_claude_qa",
            new=AsyncMock(return_value=pass_verdict),
        ),
        patch.object(dispatcher, "_git_commit_all", side_effect=_fake_commit),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()
        await asyncio.gather(*list(dispatcher._bg_tasks), return_exceptions=True)

    assert len(committed) == 1
    assert task.slug in committed[0][1]  # commit message contains the slug


# ------------------------------------------------------------------ (d) qa fail → ready


@pytest.mark.asyncio
async def test_qa_fail_moves_to_ready_and_notifies(store, project):
    """A failing QA verdict must bounce the task back to ready and fire
    qa_failed so the builder knows to fix the defect."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    notified: list[str] = []
    dispatcher._notify = lambda event, slug: notified.append(event)  # type: ignore[method-assign]

    fail_verdict = QaVerdict(
        passed=False, reason="assertion error: expected 200 got 500",
    )

    with (
        patch(
            "gateway.crew_board.dispatcher.run_claude_qa",
            new=AsyncMock(return_value=fail_verdict),
        ),
        patch.object(dispatcher, "_git_commit_all"),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()
        await asyncio.gather(*list(dispatcher._bg_tasks), return_exceptions=True)

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_READY, (
        f"expected STATUS_READY after qa fail, got {refreshed.status!r}"
    )
    assert "qa_failed" in notified


@pytest.mark.asyncio
async def test_qa_fail_leaves_comment_with_reason(store, project):
    """QA failure must leave a comment containing the verdict reason so the
    builder knows what to fix."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    fail_verdict = QaVerdict(
        passed=False, reason="missing null check on user input",
    )

    with (
        patch(
            "gateway.crew_board.dispatcher.run_claude_qa",
            new=AsyncMock(return_value=fail_verdict),
        ),
        patch.object(dispatcher, "_git_commit_all"),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()
        await asyncio.gather(*list(dispatcher._bg_tasks), return_exceptions=True)

    audit = store.audit_for(task.slug)
    comments = [a for a in audit if a.action == "comment"]
    assert any("missing null check" in (a.detail or "") for a in comments), (
        "expected QA failure reason in a comment"
    )


# ------------------------------------------------------------------ (e) qa timeout → review


@pytest.mark.asyncio
async def test_qa_timeout_promotes_to_review(store, project):
    """A task that has been in QA past QA_TIMEOUT_S must be auto-promoted
    to review with a qa_timeout notification (verify already passed)."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    notified: list[str] = []
    dispatcher._notify = lambda event, slug: notified.append(event)  # type: ignore[method-assign]

    # Make _qa_expired always return True to simulate a stale task.
    with (
        patch.object(dispatcher, "_qa_expired", return_value=True),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()
        # Timeout path is synchronous inside _tick — no spawned coroutines.

    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.status == schema.STATUS_REVIEW, (
        f"expected STATUS_REVIEW after qa timeout, got {refreshed.status!r}"
    )
    assert "qa_timeout" in notified


@pytest.mark.asyncio
async def test_qa_timeout_leaves_comment(store, project):
    """The timeout comment must mention QA and the timeout duration."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    with (
        patch.object(dispatcher, "_qa_expired", return_value=True),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()

    audit = store.audit_for(task.slug)
    comments = [a for a in audit if a.action == "comment"]
    assert any("QA timed out" in (a.detail or "") for a in comments), (
        "expected qa timeout comment"
    )


@pytest.mark.asyncio
async def test_inflight_qa_task_skipped_on_next_tick(store, project):
    """A task already in _inflight as qa:<slug> must not be dispatched again
    in a subsequent tick — single-flight guard for QA."""
    task = _qa_task(store, project)
    dispatcher = _make_dispatcher(store)

    # Mark the task as inflight before _tick runs.
    dispatcher._inflight.add(f"qa:{task.slug}")

    spawned: list = []

    def _fake_spawn(coro):
        spawned.append(coro)

    with (
        patch.object(dispatcher, "_spawn", side_effect=_fake_spawn),
        patch.object(dispatcher, "_reap_stale_in_progress"),
    ):
        await dispatcher._tick()

    # The QA inflight guard must prevent a second spawn for this task.
    qa_spawns = [c for c in spawned if hasattr(c, "__name__")
                 and "qa" in getattr(c, "__name__", "")]
    assert len(qa_spawns) == 0, "inflight qa task must not be re-spawned"


# ------------------------------------------------------------------ (f) reviewer-flow regression


def test_review_to_ready_is_legal(store, project):
    """Reviewer rejection sends review→ready. This transition must be legal
    in ALLOWED_TRANSITIONS so _run_review_body does not crash when the
    claude reviewer rejects a task."""
    task = _qa_task(store, project)
    store.move_task(task.slug, schema.STATUS_REVIEW)
    t = store.move_task(task.slug, schema.STATUS_READY)
    assert t.status == schema.STATUS_READY


def test_review_to_done_still_legal(store, project):
    """Approve path (review→done) must remain legal after the schema fix."""
    task = _qa_task(store, project)
    store.move_task(task.slug, schema.STATUS_REVIEW)
    t = store.move_task(task.slug, schema.STATUS_DONE)
    assert t.status == schema.STATUS_DONE
