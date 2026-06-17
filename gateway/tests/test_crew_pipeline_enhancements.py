"""Tests for crew-pipeline enhancements (strategy review items 1-3).

Item 1 — relevant_lessons: keyword-ranked lesson retrieval
Item 2 — pytest-tail into retry brief: prior failure tail injection
Item 3 — hive-lite escalation rung: config-driven ladder + lane cap
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from gateway.crew_board.store import CrewBoardStore, Lesson, Project
from gateway.crew_board.hive_agent_loop import (
    _build_prior_failure_tail,
    run_hive_agent_loop,
)
from gateway.crew_board.dispatcher import (
    CrewDispatcher,
    _build_ladder,
    _next_rung,
    PARALLEL_LANE_CAP,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew.db")


@pytest.fixture()
def store_and_task(tmp_path: Path):
    db = tmp_path / "vault.db"
    s = CrewBoardStore(db)
    proj = Project(
        slug="dummy", path=str(tmp_path / "proj"), name="dummy",
        enabled=True, push_allowed=False, test_cmd="python -m pytest -q",
    )
    (tmp_path / "proj").mkdir()
    s.upsert_project(proj)
    task = s.create_task(
        project_slug="dummy", title="add authentication middleware",
        body="implement JWT middleware for the auth module",
        created_by="owner",
    )
    return s, task


@dataclass
class FakeInvoker:
    replies: list[str]
    calls: list[dict] = None

    def __post_init__(self) -> None:
        self.calls = []

    async def chat(self, **kw):
        self.calls.append(kw)
        if not self.replies:
            return "{}", 0, 0
        return self.replies.pop(0), 10, 10


# ──────────────────────────────────────────────────────────────────────────────
# Item 1 — relevant_lessons
# ──────────────────────────────────────────────────────────────────────────────


def test_relevant_lessons_surfaces_keyword_match_over_newer_irrelevant(store):
    """A task title that matches an OLDER lesson should surface it ahead of
    newer but unrelated lessons.

    The scorer uses 4+-char word substrings from the task text against the
    lesson body.  So a task titled "implement authentication middleware" has
    the keyword "authentication"; a lesson body that CONTAINS that word will
    score higher than lessons that don't.
    """
    store.upsert_project(Project(slug="p", path="x", name="P"))
    # Oldest lesson: body contains the exact word from the task title
    store.add_lesson(
        "p",
        body="authentication tokens must be rotated every 24 hours",
        task_slug="t0",
    )
    # Three newer but unrelated lessons that do NOT contain "authentication"
    store.add_lesson("p", body="remember to set the database port", task_slug="t1")
    store.add_lesson("p", body="use snake_case for all variable names", task_slug="t2")
    store.add_lesson("p", body="enable prettier for code formatting", task_slug="t3")

    lessons = store.relevant_lessons(
        "p",
        task_title="implement authentication middleware",
        task_body="",
        limit=3,
    )

    # The auth-related lesson must be first even though it is the oldest.
    assert len(lessons) >= 1
    assert "authentication" in lessons[0].body


def test_relevant_lessons_respects_limit(store):
    store.upsert_project(Project(slug="p", path="x", name="P"))
    for i in range(10):
        store.add_lesson("p", body=f"lesson {i}", task_slug=f"t{i}")
    lessons = store.relevant_lessons("p", task_title="lesson", task_body="", limit=3)
    assert len(lessons) == 3


def test_relevant_lessons_empty_project_returns_empty(store):
    store.upsert_project(Project(slug="p", path="x", name="P"))
    lessons = store.relevant_lessons("p", task_title="auth", task_body="", limit=3)
    assert lessons == []


def test_relevant_lessons_recency_tiebreaker(store):
    """When two lessons have the same keyword score, the newer one wins."""
    store.upsert_project(Project(slug="p", path="x", name="P"))
    store.add_lesson("p", body="cache invalidation tip", task_slug="t1")
    store.add_lesson("p", body="cache eviction strategy", task_slug="t2")

    lessons = store.relevant_lessons(
        "p", task_title="cache", task_body="", limit=2,
    )
    # Both match equally; the newer (t2) should come first due to recency.
    assert len(lessons) == 2
    assert lessons[0].task_slug == "t2"


# ──────────────────────────────────────────────────────────────────────────────
# Item 2 — pytest-tail into retry brief
# ──────────────────────────────────────────────────────────────────────────────


def test_build_prior_failure_tail_returns_empty_on_no_output():
    result = _build_prior_failure_tail({"exit_code": 1, "stdout_tail": "",
                                         "stderr_tail": ""})
    assert result == ""


def test_build_prior_failure_tail_includes_stdout():
    vr = {
        "exit_code": 1,
        "stdout_tail": "FAILED tests/test_foo.py::test_bar - AssertionError",
        "stderr_tail": "",
    }
    out = _build_prior_failure_tail(vr)
    assert "FAILED" in out
    assert "AssertionError" in out
    assert "PRIOR ATTEMPT FAILURE" in out


def test_build_prior_failure_tail_trims_to_cap():
    long_output = "x" * 5000
    vr = {"exit_code": 1, "stdout_tail": long_output, "stderr_tail": ""}
    out = _build_prior_failure_tail(vr)
    # Should be well under 5000 characters
    assert len(out) < 4000


def test_build_prior_failure_tail_combines_stdout_and_stderr():
    vr = {
        "exit_code": 2,
        "stdout_tail": "FAILED test_foo",
        "stderr_tail": "ModuleNotFoundError: no module named bar",
    }
    out = _build_prior_failure_tail(vr)
    assert "FAILED test_foo" in out
    assert "ModuleNotFoundError" in out


@pytest.mark.asyncio
async def test_loop_injects_failure_tail_when_prior_verify_failed(
    store_and_task,
) -> None:
    """A task with stored failing verify_results gets the failure tail in
    its brief.  The hive_agent_loop builds the brief from history[0] and
    passes it as the `user=` kwarg to invoker.chat; the FakeInvoker records
    all calls so we can inspect the first user message."""
    store, task = store_and_task

    # Simulate a prior failed verify run by writing verify_results.
    store.update_verify_results(task.slug, {
        "ok": False,
        "tests": {
            "ran": True,
            "exit_code": 1,
            "stdout_tail": "FAILED tests/test_auth.py::test_jwt_invalid - AssertionError: expected 401",
            "stderr_tail": "",
        },
    })
    # Re-fetch so the task object carries the updated verify_results.
    task = store.get_task(task.slug)

    invoker = FakeInvoker(replies=[
        json.dumps({"tool": "done", "args": {"summary": "done"}}),
    ])

    await run_hive_agent_loop(store, task, invoker=invoker, max_iters=5)

    # FakeInvoker.calls records every kwarg dict passed to chat().
    assert invoker.calls, "no chat call was made"
    first_user_msg = invoker.calls[0].get("user", "")
    assert "PRIOR ATTEMPT FAILURE" in first_user_msg, (
        f"failure tail not found in first user msg: {first_user_msg[:500]!r}"
    )
    assert "test_jwt_invalid" in first_user_msg


@pytest.mark.asyncio
async def test_loop_no_failure_tail_when_prior_verify_passed(
    store_and_task,
) -> None:
    """When the prior verify succeeded (exit_code=0), no failure tail is
    injected."""
    store, task = store_and_task

    store.update_verify_results(task.slug, {
        "ok": True,
        "tests": {
            "ran": True,
            "exit_code": 0,
            "stdout_tail": "5 passed",
            "stderr_tail": "",
        },
    })
    task = store.get_task(task.slug)

    invoker = FakeInvoker(replies=[
        json.dumps({"tool": "done", "args": {"summary": "done"}}),
    ])

    await run_hive_agent_loop(store, task, invoker=invoker, max_iters=5)

    assert invoker.calls, "no chat call was made"
    first_user_msg = invoker.calls[0].get("user", "")
    assert "PRIOR ATTEMPT FAILURE" not in first_user_msg


# ──────────────────────────────────────────────────────────────────────────────
# Item 3 — hive-lite escalation rung plumbing
# ──────────────────────────────────────────────────────────────────────────────


def test_build_ladder_default_two_rung():
    ladder = _build_ladder(hive_lite_enabled=False)
    assert ladder == ["hive", "claude-code"]


def test_build_ladder_three_rung_when_enabled():
    ladder = _build_ladder(hive_lite_enabled=True)
    assert ladder == ["hive", "hive-lite", "claude-code"]


def test_next_rung_two_rung():
    ladder = _build_ladder(hive_lite_enabled=False)
    assert _next_rung("hive", ladder) == "claude-code"
    assert _next_rung("claude-code", ladder) is None


def test_next_rung_three_rung():
    ladder = _build_ladder(hive_lite_enabled=True)
    assert _next_rung("hive", ladder) == "hive-lite"
    assert _next_rung("hive-lite", ladder) == "claude-code"
    assert _next_rung("claude-code", ladder) is None


def test_next_rung_unknown_assignee_returns_none():
    ladder = _build_ladder()
    assert _next_rung("unknown-worker", ladder) is None


def test_dispatcher_three_rung_when_enabled(tmp_path: Path) -> None:
    """CrewDispatcher built with hive_lite_enabled=True has a 3-rung ladder."""
    store = CrewBoardStore(tmp_path / "crew.db")
    disp = CrewDispatcher(
        store, None,
        hive_lite_enabled=True,
        hive_lite_model="qwen2.5:7b",
    )
    assert disp._escalation_ladder == ["hive", "hive-lite", "claude-code"]
    assert disp._hive_lite_model == "qwen2.5:7b"


def test_dispatcher_two_rung_by_default(tmp_path: Path) -> None:
    """CrewDispatcher without hive_lite config stays 2-rung (no behaviour change)."""
    store = CrewBoardStore(tmp_path / "crew.db")
    disp = CrewDispatcher(store, None)
    assert disp._escalation_ladder == ["hive", "claude-code"]
    assert disp._hive_lite_model is None


def test_dispatcher_lane_cap_reads_config(tmp_path: Path) -> None:
    """parallel_lane_cap kwarg is stored on the dispatcher instance."""
    store = CrewBoardStore(tmp_path / "crew.db")
    disp = CrewDispatcher(store, None, parallel_lane_cap=2)
    assert disp._parallel_lane_cap == 2


def test_dispatcher_lane_cap_default_is_one(tmp_path: Path) -> None:
    store = CrewBoardStore(tmp_path / "crew.db")
    disp = CrewDispatcher(store, None)
    assert disp._parallel_lane_cap == 1


def test_config_hive_lite_keys_parse_correctly(tmp_path: Path) -> None:
    """load_config correctly parses the three new crew_hive_lite / lane keys."""
    from gateway.config import load_config

    cfg_file = tmp_path / "gateway.yaml"
    cfg_file.write_text(
        "bind_host: 127.0.0.1\n"
        "bind_port: 8766\n"
        "state_dir: /tmp/state\n"
        "vault_path: /tmp/vault\n"
        "vault_writer:\n"
        "  host: 127.0.0.1\n"
        "  port: 8765\n"
        "  token_path: /tmp/token\n"
        "crew_hive_lite_enabled: true\n"
        "crew_hive_lite_model: qwen2.5:7b\n"
        "crew_parallel_lane_cap: 3\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.crew_hive_lite_enabled is True
    assert cfg.crew_hive_lite_model == "qwen2.5:7b"
    assert cfg.crew_parallel_lane_cap == 3


def test_config_hive_lite_defaults_off(tmp_path: Path) -> None:
    """Keys absent from YAML produce the safe defaults (feature off)."""
    from gateway.config import load_config

    cfg_file = tmp_path / "gateway.yaml"
    cfg_file.write_text(
        "bind_host: 127.0.0.1\n"
        "bind_port: 8766\n"
        "state_dir: /tmp/state\n"
        "vault_path: /tmp/vault\n"
        "vault_writer:\n"
        "  host: 127.0.0.1\n"
        "  port: 8765\n"
        "  token_path: /tmp/token\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.crew_hive_lite_enabled is False
    assert cfg.crew_hive_lite_model is None
    assert cfg.crew_parallel_lane_cap == 1
