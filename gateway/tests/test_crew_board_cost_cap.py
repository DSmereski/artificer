"""Tests for the daily escalation cost cap.

Verifies that when the rolling 24h claude spend exceeds
crew_escalation_daily_usd_cap the dispatcher parks a hive-failing task
in review instead of promoting it to claude-code.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.crew_board import schema
from gateway.crew_board.dispatcher import ESCALATION_THRESHOLD, CrewDispatcher
from gateway.crew_board.store import CrewBoardStore, Project, Task


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def store(tmp_path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew.db")


@pytest.fixture
def project(store) -> object:
    return store.upsert_project(
        Project(slug="sc", path="/tmp/sc", name="StarCraft", enabled=True)
    )


def _make_dispatcher(store, *, cap: float | None = 20.0) -> CrewDispatcher:
    return CrewDispatcher(
        store,
        coordinator=MagicMock(),
        vault_path=None,
        notifier=None,
        daily_usd_cap=cap,
    )


def _make_task_at_escalation_threshold(store, project) -> Task:
    """Create a task that is ready, assigned to hive, and has exactly
    ESCALATION_THRESHOLD attempts already logged (so the NEXT failure
    would trigger escalation)."""
    task = store.create_task(
        title="test task",
        project_slug=project.slug,
        created_by="owner",
    )
    # Move to backlog → ready, assign to hive.
    store.move_task(task.slug, schema.STATUS_READY, actor="owner")
    store.assign_task(task.slug, "hive", actor="owner")
    # Simulate ESCALATION_THRESHOLD prior failed attempts.
    for _ in range(ESCALATION_THRESHOLD):
        store.increment_attempt(task.slug)
    return store.get_task(task.slug)


# ---------------------------------------------------------------- helpers

def _inject_rolling_cost(store, usd: float, *, usd_per_million: float = 6.0) -> None:
    """Fake the rolling 24h cost by patching the store method."""
    store.rolling_24h_claude_cost_usd = MagicMock(return_value=usd)


# ---------------------------------------------------------------- tests

def test_task_parks_in_review_when_cap_exceeded(store, project):
    """With a $1 cap and $5 already spent, a hive failure that would
    escalate to claude-code must park in review instead."""
    task = _make_task_at_escalation_threshold(store, project)
    dispatcher = _make_dispatcher(store, cap=1.0)
    _inject_rolling_cost(store, usd=5.0)  # well over the $1 cap

    # Simulate what the dispatcher does on failure-after-threshold:
    # call the escalation block logic directly by running a trimmed _run_task
    # cycle. We mock the runners so no actual subprocesses run.

    loop_result = MagicMock(ok=False, turns=3, reason="test failure")
    verify_result = MagicMock(ok=False, reason="test verify failed")

    async def _fake_run(*a, **kw):
        return loop_result

    with (
        patch("gateway.crew_board.dispatcher.run_hive_agent_loop") as mock_run,
        patch("gateway.crew_board.dispatcher.verify") as mock_verify,
    ):
        mock_run.side_effect = _fake_run
        mock_verify.return_value = verify_result
        asyncio.run(dispatcher._run_task(task.slug))

    final = store.get_task(task.slug)
    assert final is not None
    assert final.status == schema.STATUS_REVIEW, (
        f"Expected review (budget exceeded), got {final.status!r}"
    )
    # Confirm the comment mentions the budget.
    audit = store.audit_for(task.slug)
    comments = [e.detail for e in audit if e.action == "comment"]
    assert any("budget" in c or "exceeded" in c for c in comments), (
        f"No budget-exceeded comment found; comments: {comments}"
    )


def test_task_escalates_normally_when_under_cap(store, project):
    """With $25 cap and $5 spent, escalation proceeds normally."""
    task = _make_task_at_escalation_threshold(store, project)
    dispatcher = _make_dispatcher(store, cap=25.0)
    _inject_rolling_cost(store, usd=5.0)  # under the $25 cap

    loop_result = MagicMock(ok=False, turns=3, reason="test failure")
    verify_result = MagicMock(ok=False, reason="test verify failed")

    async def _fake_run(*a, **kw):
        return loop_result

    with (
        patch("gateway.crew_board.dispatcher.run_hive_agent_loop") as mock_run,
        patch("gateway.crew_board.dispatcher.verify") as mock_verify,
    ):
        mock_run.side_effect = _fake_run
        mock_verify.return_value = verify_result
        asyncio.run(dispatcher._run_task(task.slug))

    final = store.get_task(task.slug)
    assert final is not None
    # Should have escalated: assignee = claude-code, status = ready
    assert final.assignee == "claude-code", (
        f"Expected claude-code assignee after escalation, got {final.assignee!r}"
    )
    assert final.status == schema.STATUS_READY, (
        f"Expected ready status after escalation, got {final.status!r}"
    )


def test_no_cap_unlimited_escalation(store, project):
    """With cap=None (unlimited), escalation always proceeds."""
    task = _make_task_at_escalation_threshold(store, project)
    dispatcher = _make_dispatcher(store, cap=None)
    _inject_rolling_cost(store, usd=9999.0)  # enormous spend

    loop_result = MagicMock(ok=False, turns=3, reason="test failure")
    verify_result = MagicMock(ok=False, reason="test verify failed")

    async def _fake_run(*a, **kw):
        return loop_result

    with (
        patch("gateway.crew_board.dispatcher.run_hive_agent_loop") as mock_run,
        patch("gateway.crew_board.dispatcher.verify") as mock_verify,
    ):
        mock_run.side_effect = _fake_run
        mock_verify.return_value = verify_result
        asyncio.run(dispatcher._run_task(task.slug))

    final = store.get_task(task.slug)
    assert final is not None
    assert final.assignee == "claude-code"
    assert final.status == schema.STATUS_READY


def test_rolling_24h_cost_method_returns_zero_on_empty_store(store):
    """The store method must not crash on an empty DB."""
    cost = store.rolling_24h_claude_cost_usd()
    assert cost == 0.0


def test_rolling_24h_cost_method_converts_tokens_to_usd(store, project):
    """Store method correctly converts token count to USD at the default rate."""
    task = store.create_task(
        title="cost test", project_slug=project.slug, created_by="owner",
    )
    # Add 1 000 000 claude tokens → $6.00 at the default rate.
    store.add_tokens(task.slug, kind="claude", n=1_000_000)
    cost = store.rolling_24h_claude_cost_usd()
    assert abs(cost - 6.0) < 0.01, f"Expected $6.00, got ${cost:.4f}"
