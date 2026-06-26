"""Env-broken gate.

A configured ``test_cmd`` that cannot spawn (Windows ``WinError 2`` on a
``.bat`` shim) or a missing project path is a broken *environment*, not a
failing *agent*. Retrying it just burns attempts and tokens: T-0301
('Publish all Android apps to the Sample Android store') wasted all 5
attempts and 144k claude tokens because ``flutter test`` could not resolve
its shim under the gateway's minimal PATH, so every attempt hard-failed the
verify gate identically and the work was rolled back each time.

The dispatcher must detect this class of verdict and park the task in
review for the owner *immediately* — without consuming the remaining
attempts or escalating to the paid claude rung.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gateway.crew_board import schema
from gateway.crew_board.dispatcher import CrewDispatcher, _verdict_env_broken
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


def _make_dispatcher(store) -> CrewDispatcher:
    return CrewDispatcher(
        store,
        coordinator=MagicMock(),
        vault_path=None,
        notifier=None,
        daily_usd_cap=20.0,
    )


def _ready_hive_task(store, project) -> Task:
    task = store.create_task(
        title="env-broken task",
        project_slug=project.slug,
        created_by="owner",
    )
    store.move_task(task.slug, schema.STATUS_READY, actor="owner")
    store.assign_task(task.slug, "hive", actor="owner")
    return store.get_task(task.slug)


def _spawn_fail_verdict() -> MagicMock:
    """A verdict shaped exactly like T-0301's last verify_results: the
    configured ``test_cmd`` could not spawn."""
    return MagicMock(
        ok=False,
        reason=(
            "tests could not run: could not spawn: [WinError 2] The system "
            "cannot find the file specified; (owner-review) 5 criteria unchecked"
        ),
        tests={
            "ran": False,
            "reason": (
                "could not spawn: [WinError 2] The system cannot find the "
                "file specified"
            ),
            "exit_code": None,
        },
    )


def _run_one_attempt(dispatcher, store, verdict) -> None:
    loop_result = MagicMock(ok=False, turns=1, reason="")

    async def _fake_run(*a, **kw):
        return loop_result

    with (
        patch("gateway.crew_board.dispatcher.run_hive_agent_loop") as mock_run,
        patch("gateway.crew_board.dispatcher.verify") as mock_verify,
    ):
        mock_run.side_effect = _fake_run
        mock_verify.return_value = verdict
        asyncio.run(dispatcher._run_task("T-0001"))  # noqa: F841


# ---------------------------------------------------------------- tests

def test_env_broken_parks_in_review_on_first_attempt(store, project):
    """A spawn-failure verdict on the FIRST attempt parks in review
    immediately rather than bouncing back to ready for a doomed retry."""
    task = _ready_hive_task(store, project)
    dispatcher = _make_dispatcher(store)
    _run_one_attempt(dispatcher, store, _spawn_fail_verdict())

    final = store.get_task(task.slug)
    assert final is not None
    assert final.status == schema.STATUS_REVIEW, (
        f"env-broken must park in review, got {final.status!r}"
    )


def test_env_broken_does_not_escalate_to_paid_rung(store, project):
    """Parking for a broken environment must NOT promote the task to the
    paid claude-code rung — the environment, not the agent, is at fault."""
    task = _ready_hive_task(store, project)
    dispatcher = _make_dispatcher(store)
    _run_one_attempt(dispatcher, store, _spawn_fail_verdict())

    final = store.get_task(task.slug)
    assert final is not None
    assert final.assignee == "hive", (
        f"env-broken must not escalate; assignee={final.assignee!r}"
    )


def test_env_broken_comment_explains_environment(store, project):
    """The audit trail must say the environment was broken, so the owner
    knows it is an infra fix, not a code fix."""
    task = _ready_hive_task(store, project)
    dispatcher = _make_dispatcher(store)
    _run_one_attempt(dispatcher, store, _spawn_fail_verdict())

    audit = store.audit_for(task.slug)
    comments = [e.detail or "" for e in audit if e.action == "comment"]
    assert any("environment" in c.lower() for c in comments), (
        f"expected a broken-environment comment; got {comments}"
    )


def test_missing_project_path_parks_in_review(store, project):
    """The other env-broken branch: verify could not find the project path.
    Like a spawn failure, this is infra, not the agent — park, don't retry."""
    task = _ready_hive_task(store, project)
    dispatcher = _make_dispatcher(store)
    verdict = MagicMock(
        ok=False,
        reason="tests could not run: project path missing",
        tests={
            "ran": False,
            "reason": "project path missing: /tmp/sc",
            "exit_code": None,
        },
    )
    _run_one_attempt(dispatcher, store, verdict)

    final = store.get_task(task.slug)
    assert final is not None
    assert final.status == schema.STATUS_REVIEW, (
        f"missing project path must park in review, got {final.status!r}"
    )


def test_verdict_env_broken_predicate():
    """Unit-level guard on the predicate itself: it fires only when the
    test command never ran AND the reason names a spawn/path fault."""
    spawn = MagicMock(tests={"ran": False, "reason": "could not spawn: x"})
    path = MagicMock(tests={"ran": False, "reason": "project path missing: y"})
    ran_and_failed = MagicMock(tests={"ran": True, "reason": ""})
    other_no_run = MagicMock(tests={"ran": False, "reason": "timed out"})
    no_tests = MagicMock(tests=None)

    assert _verdict_env_broken(spawn) is True
    assert _verdict_env_broken(path) is True
    assert _verdict_env_broken(ran_and_failed) is False
    assert _verdict_env_broken(other_no_run) is False
    assert _verdict_env_broken(no_tests) is False


def test_genuine_test_failure_still_retries(store, project):
    """Regression guard: a real test failure (tests ran, non-zero exit) is
    NOT env-broken — it must still bounce back to ready for a retry."""
    task = _ready_hive_task(store, project)
    dispatcher = _make_dispatcher(store)
    verdict = MagicMock(
        ok=False,
        reason="tests failed (exit=1)",
        tests={"ran": True, "exit_code": 1, "reason": ""},
    )
    _run_one_attempt(dispatcher, store, verdict)

    final = store.get_task(task.slug)
    assert final is not None
    assert final.status == schema.STATUS_READY, (
        f"a genuine test failure must retry (ready), got {final.status!r}"
    )
