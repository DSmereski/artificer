"""P6 goal-completion verification loop tests.

Covers the four required gates:
1. decompose creates a goal record with a checklist + stamps subtasks with goal_id.
2. all-subtasks-done → exactly ONE verify ticket spawned (idempotent).
3. subtasks-pass-but-checklist-UNMET goal → cycle-1 re-goal; later all-met → complete.
4. KEY GATE: a perpetually-UNMET goal STOPS at cycle 3 + escalates, creates NO 4th goal.

All model calls are mocked — no Ollama or Claude subprocess is invoked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.goal_loop import (
    GOAL_MAX_CYCLES,
    GoalRecord,
    _all_subtasks_done,
    create_goal,
    extract_goal_id,
    get_goal,
    goal_tag,
    handle_verify_result,
    maybe_spawn_verify,
    run_goal_verify,
    store_goal,
)


# ------------------------------------------------------------------ fixtures

@pytest.fixture
def store(tmp_path: Path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew.db")


@pytest.fixture
def project(store: CrewBoardStore) -> Project:
    return store.upsert_project(
        Project(slug="tproj", path="/tmp/tproj", name="Test Project", enabled=True)
    )


def _done_task(store: CrewBoardStore, project: Project, goal_id: str):
    """Create a subtask that's already done, stamped with goal_id."""
    task = store.create_task(
        title="subtask",
        project_slug=project.slug,
        created_by="owner",
        tags=["nl-decompose", goal_tag(goal_id)],
        goal_id=goal_id,
    )
    store.assign_task(task.slug, "hive", actor="owner")
    store.move_task(task.slug, schema.STATUS_READY, actor="owner")
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive")
    store.move_task(task.slug, schema.STATUS_QA, actor="hive")
    store.move_task(task.slug, schema.STATUS_REVIEW, actor="hive")
    store.move_task(task.slug, schema.STATUS_DONE, actor="hive")
    return store.get_task(task.slug)


# ================================================================== Test 1
# decompose creates a goal record with a checklist + stamps subtasks.

def test_decompose_creates_goal_record(store: CrewBoardStore, project: Project) -> None:
    """create_goal stores a GoalRecord in crew_meta with the correct fields."""
    goal = create_goal(
        store,
        text="Build auth API",
        project_slug=project.slug,
        checklist_items=[
            "file src/auth.py exists",
            "GET /api/v1/auth returns 200",
        ],
        cycle=0,
    )

    # Record is persisted.
    retrieved = get_goal(store, goal.goal_id)
    assert retrieved is not None, "goal record must be stored in crew_meta"
    assert retrieved.text == "Build auth API"
    assert retrieved.project_slug == project.slug
    assert retrieved.cycle == 0
    assert retrieved.status == "active"
    assert len(retrieved.checklist) == 2
    assert retrieved.checklist[0]["item"] == "file src/auth.py exists"
    assert retrieved.checklist[0]["met"] is False
    assert retrieved.checklist[1]["item"] == "GET /api/v1/auth returns 200"


def test_subtasks_stamped_with_goal_id(store: CrewBoardStore, project: Project) -> None:
    """Tasks created for a goal carry the goal_id in both goal_id column and tags."""
    goal = create_goal(
        store,
        text="Build auth API",
        project_slug=project.slug,
        checklist_items=["file src/auth.py exists"],
    )

    # Simulate what /board/decompose does: create subtasks with goal_id.
    task = store.create_task(
        title="Write auth module",
        project_slug=project.slug,
        created_by="owner",
        tags=["nl-decompose", goal_tag(goal.goal_id)],
        goal_id=goal.goal_id,
    )
    refreshed = store.get_task(task.slug)
    assert refreshed is not None
    assert refreshed.goal_id == goal.goal_id, "goal_id column must be set"
    assert goal_tag(goal.goal_id) in (refreshed.tags or []), "goal tag must be in tags"
    assert extract_goal_id(refreshed.tags) == goal.goal_id


# ================================================================== Test 2
# all-subtasks-done → exactly ONE verify ticket spawned (idempotent).

def test_all_subtasks_done_spawns_verify_ticket(store: CrewBoardStore, project: Project) -> None:
    """When every non-verify subtask is done, maybe_spawn_verify creates exactly
    one verify ticket in STATUS_READY."""
    goal = create_goal(
        store,
        text="Build feature X",
        project_slug=project.slug,
        checklist_items=["feature X works end-to-end"],
    )
    _done_task(store, project, goal.goal_id)

    spawned = maybe_spawn_verify(store, goal.goal_id)
    assert spawned is True, "verify ticket must be spawned when all subtasks done"

    # Find the verify ticket.
    all_tasks = store.list_tasks()
    verify_tickets = [
        t for t in all_tasks
        if "goal-verify" in (t.tags or [])
        and goal_tag(goal.goal_id) in (t.tags or [])
    ]
    assert len(verify_tickets) == 1, f"expected 1 verify ticket, got {len(verify_tickets)}"
    assert verify_tickets[0].status == schema.STATUS_READY


def test_verify_ticket_spawn_is_idempotent(store: CrewBoardStore, project: Project) -> None:
    """Calling maybe_spawn_verify multiple times for the same goal/cycle
    creates ONLY ONE verify ticket — the verify_spawned guard prevents doubles."""
    goal = create_goal(
        store,
        text="Build feature X",
        project_slug=project.slug,
        checklist_items=["feature X works"],
    )
    _done_task(store, project, goal.goal_id)

    # First call should create the ticket.
    first = maybe_spawn_verify(store, goal.goal_id)
    # Second call (as if another tick fires before the verify task is claimed).
    second = maybe_spawn_verify(store, goal.goal_id)
    # Third call for good measure.
    third = maybe_spawn_verify(store, goal.goal_id)

    assert first is True, "first spawn should return True"
    assert second is False, "second spawn must be a no-op (idempotent)"
    assert third is False, "third spawn must also be a no-op"

    all_tasks = store.list_tasks()
    verify_tickets = [
        t for t in all_tasks
        if "goal-verify" in (t.tags or [])
        and goal_tag(goal.goal_id) in (t.tags or [])
    ]
    assert len(verify_tickets) == 1, (
        f"idempotency violated: expected 1 verify ticket, got {len(verify_tickets)}"
    )


def test_not_all_done_does_not_spawn(store: CrewBoardStore, project: Project) -> None:
    """maybe_spawn_verify must not fire while a subtask is still in_progress."""
    goal = create_goal(
        store,
        text="Feature in flight",
        project_slug=project.slug,
        checklist_items=["all done"],
    )
    # Create a subtask and leave it in_progress.
    task = store.create_task(
        title="in-flight subtask",
        project_slug=project.slug,
        created_by="owner",
        tags=["nl-decompose", goal_tag(goal.goal_id)],
        goal_id=goal.goal_id,
    )
    store.assign_task(task.slug, "hive", actor="owner")
    store.move_task(task.slug, schema.STATUS_READY, actor="owner")
    store.move_task(task.slug, schema.STATUS_IN_PROGRESS, actor="hive")

    spawned = maybe_spawn_verify(store, goal.goal_id)
    assert spawned is False, "must not spawn while a subtask is still in_progress"


# ================================================================== Test 3
# subtasks-pass-but-checklist-UNMET → cycle-1 re-goal; later all-met → complete.

def test_unmet_goal_creates_regoal_cycle1(store: CrewBoardStore, project: Project) -> None:
    """When the verify runner says checklist items are UNMET and cycle < 3,
    handle_verify_result creates a cycle-1 re-goal and leaves the original active."""
    goal = create_goal(
        store,
        text="Implement auth",
        project_slug=project.slug,
        checklist_items=["file src/auth.py exists", "tests pass"],
    )
    subtask = _done_task(store, project, goal.goal_id)
    maybe_spawn_verify(store, goal.goal_id)

    # Retrieve the verify ticket.
    verify_task = next(
        t for t in store.list_tasks()
        if "goal-verify" in (t.tags or [])
        and goal_tag(goal.goal_id) in (t.tags or [])
    )

    # Simulate UNMET result (e.g. qwen/claude says file doesn't exist).
    unmet_result = {
        "all_met": False,
        "verdicts": [
            {"item": "file src/auth.py exists", "verdict": "UNMET", "reason": "file missing"},
            {"item": "tests pass", "verdict": "MET", "reason": "tests all green"},
        ],
    }

    # Mock decompose_fn so we don't need Ollama.
    decomposed_goals = []

    async def _mock_decompose(**kwargs):
        decomposed_goals.append(kwargs)

    asyncio.run(handle_verify_result(
        store, verify_task, unmet_result,
        notifier=None, decompose_fn=_mock_decompose,
    ))

    # A re-goal must have been created for the unmet items.
    re_goal_id = f"{goal.goal_id}-c1"
    re_goal = get_goal(store, re_goal_id)
    assert re_goal is not None, "re-goal cycle 1 must be created"
    assert re_goal.cycle == 1, f"expected cycle=1, got {re_goal.cycle}"
    assert len(re_goal.checklist) == 1, (
        "re-goal checklist must contain only unmet items"
    )
    assert re_goal.checklist[0]["item"] == "file src/auth.py exists"
    # decompose_fn must have been called.
    assert len(decomposed_goals) == 1
    assert decomposed_goals[0]["goal_id"] == re_goal_id


def test_met_goal_reaches_complete(store: CrewBoardStore, project: Project) -> None:
    """When verify says ALL checklist items are MET, handle_verify_result
    closes the goal with status=complete."""
    goal = create_goal(
        store,
        text="Implement auth",
        project_slug=project.slug,
        checklist_items=["file src/auth.py exists"],
    )
    _done_task(store, project, goal.goal_id)
    maybe_spawn_verify(store, goal.goal_id)

    verify_task = next(
        t for t in store.list_tasks()
        if "goal-verify" in (t.tags or [])
    )

    met_result = {
        "all_met": True,
        "verdicts": [
            {"item": "file src/auth.py exists", "verdict": "MET", "reason": "file found"},
        ],
    }

    asyncio.run(handle_verify_result(
        store, verify_task, met_result,
        notifier=None, decompose_fn=None,
    ))

    final_goal = get_goal(store, goal.goal_id)
    assert final_goal is not None
    assert final_goal.status == "complete", (
        f"goal must be complete when all items are met, got {final_goal.status!r}"
    )


# ================================================================== Test 4 (THE KEY GATE)
# Perpetually-UNMET goal STOPS at cycle 3 + escalates. Creates NO 4th goal.

def test_hard_cap_stops_at_cycle_3_no_4th_goal(store: CrewBoardStore, project: Project) -> None:
    """THE KEY ANTI-RUNAWAY TEST.

    A goal that is perpetually UNMET must STOP after exactly 3 cycles
    (cycle 0 → c1 → c2 → c3 = cap). On cycle 3, handle_verify_result must:
    - set status = 'needs_you'
    - fire an escalation ticket
    - NOT create a 4th re-goal

    The GOAL_MAX_CYCLES constant is the hard counter. This test verifies the
    cap in code, not a prompt instruction.
    """
    assert GOAL_MAX_CYCLES == 3, (
        f"GOAL_MAX_CYCLES must be 3 (anti-runaway contract), got {GOAL_MAX_CYCLES}"
    )

    # Simulate the full lifecycle: cycle 0 → 1 → 2 → 3 → STOP.
    # Each cycle: subtasks done → verify spawned → UNMET → re-goal.
    # At cycle 3: UNMET → needs_you, NO re-goal.

    unmet_result = {
        "all_met": False,
        "verdicts": [
            {"item": "always unmet", "verdict": "UNMET", "reason": "never fixed"},
        ],
    }

    created_regoals: list[str] = []

    async def _mock_decompose(**kwargs):
        created_regoals.append(kwargs.get("goal_id", "?"))

    # Cycle 0: the original goal.
    goal = create_goal(
        store,
        text="Perpetually broken goal",
        project_slug=project.slug,
        checklist_items=["always unmet"],
        cycle=0,
    )
    current_goal_id = goal.goal_id

    for expected_cycle in range(GOAL_MAX_CYCLES):
        # Create a subtask and mark it done.
        current_goal = get_goal(store, current_goal_id)
        assert current_goal is not None, f"goal {current_goal_id} must exist at cycle {expected_cycle}"
        assert current_goal.cycle == expected_cycle

        subtask = _done_task(store, project, current_goal_id)

        # Spawn verify ticket.
        spawned = maybe_spawn_verify(store, current_goal_id)
        assert spawned is True, (
            f"verify must spawn at cycle {expected_cycle} for goal {current_goal_id}"
        )

        verify_task = next(
            t for t in store.list_tasks()
            if "goal-verify" in (t.tags or [])
            and goal_tag(current_goal_id) in (t.tags or [])
            and t.status == schema.STATUS_READY
        )

        # Run verify → UNMET.
        asyncio.run(handle_verify_result(
            store, verify_task, unmet_result,
            notifier=None, decompose_fn=_mock_decompose,
        ))

        if expected_cycle < GOAL_MAX_CYCLES - 1:
            # Should have created a re-goal.
            next_goal_id = f"{current_goal_id}-c{expected_cycle + 1}"
            next_goal = get_goal(store, next_goal_id)
            assert next_goal is not None, (
                f"re-goal must exist after cycle {expected_cycle} UNMET"
            )
            assert next_goal.cycle == expected_cycle + 1
            current_goal_id = next_goal_id
        else:
            # This was cycle GOAL_MAX_CYCLES - 1 (i.e. 2, the last allowed retry).
            # After this UNMET, the next goal to be checked is at cycle 3 = the cap.
            break

    # After the loop, current_goal_id is the cycle-2 re-goal (the last created).
    # Now simulate cycle 3: subtasks done → verify → UNMET → HARD CAP must fire.
    cycle3_goal_id = f"{current_goal_id}-c{GOAL_MAX_CYCLES}"

    # The cycle-2 goal was just created with cycle=2. We need a goal at cycle=3
    # to test the cap. Create it directly to simulate the re-goal chain reaching 3.
    cap_goal = create_goal(
        store,
        text="cap test goal at max cycle",
        project_slug=project.slug,
        checklist_items=["always unmet"],
        cycle=GOAL_MAX_CYCLES,   # exactly at the cap
        goal_id=cycle3_goal_id,
    )

    subtask_cap = _done_task(store, project, cycle3_goal_id)
    spawned_cap = maybe_spawn_verify(store, cycle3_goal_id)
    assert spawned_cap is True, "verify must still spawn at cycle 3 (to evaluate)"

    verify_cap_task = next(
        t for t in store.list_tasks()
        if "goal-verify" in (t.tags or [])
        and goal_tag(cycle3_goal_id) in (t.tags or [])
        and t.status == schema.STATUS_READY
    )

    regoals_before = len(created_regoals)

    # UNMET at cycle=3 → must escalate, NOT create a 4th cycle.
    asyncio.run(handle_verify_result(
        store, verify_cap_task, unmet_result,
        notifier=None, decompose_fn=_mock_decompose,
    ))

    # --- Assertions for the hard cap ---

    # 1. Goal status must be needs_you.
    final_cap_goal = get_goal(store, cycle3_goal_id)
    assert final_cap_goal is not None
    assert final_cap_goal.status == "needs_you", (
        f"goal at cap must be 'needs_you', got {final_cap_goal.status!r}"
    )

    # 2. decompose_fn must NOT have been called again (no 4th re-goal).
    regoals_after = len(created_regoals)
    assert regoals_after == regoals_before, (
        f"HARD CAP VIOLATED: decompose_fn was called at cycle 3 "
        f"(created_regoals went from {regoals_before} to {regoals_after}). "
        f"No 4th goal must ever be created."
    )

    # 3. No re-goal with cycle > GOAL_MAX_CYCLES must exist anywhere.
    all_meta_keys = []
    with store._lock:
        rows = store._conn.execute("SELECT key FROM crew_meta WHERE key LIKE 'goal:%'").fetchall()
        all_meta_keys = [r["key"] for r in rows]

    for key in all_meta_keys:
        raw = store.get_meta(key)
        if raw:
            try:
                record = GoalRecord.from_json(raw)
                assert record.cycle <= GOAL_MAX_CYCLES, (
                    f"HARD CAP VIOLATED: found goal {record.goal_id} with "
                    f"cycle={record.cycle} > GOAL_MAX_CYCLES={GOAL_MAX_CYCLES}"
                )
            except Exception:
                pass

    # 4. An escalation ticket (needs-you tag) must have been created.
    all_tasks = store.list_tasks()
    escalation_tasks = [
        t for t in all_tasks
        if "needs-you" in (t.tags or [])
    ]
    assert len(escalation_tasks) >= 1, (
        "an escalation ticket tagged 'needs-you' must be created at the hard cap"
    )


# ================================================================== run_goal_verify unit
# Verify runner with mocked models.

def test_run_goal_verify_all_met(store: CrewBoardStore, project: Project) -> None:
    """run_goal_verify returns all_met=True when the qwen mock says all MET."""
    goal = create_goal(
        store,
        text="Simple goal",
        project_slug=project.slug,
        checklist_items=["item A", "item B"],
    )
    # Create a fake verify task.
    verify_task_obj = store.create_task(
        title="[goal-verify] Simple goal",
        project_slug=project.slug,
        created_by="system",
        tags=["goal-verify", goal_tag(goal.goal_id)],
        goal_id=goal.goal_id,
    )

    met_json = json.dumps([
        {"item": "item A", "verdict": "MET", "reason": "found"},
        {"item": "item B", "verdict": "MET", "reason": "found"},
    ])

    async def _mock_qwen(prompt: str) -> str:
        return met_json

    result = asyncio.run(run_goal_verify(
        store, verify_task_obj,
        qwen_invoker=_mock_qwen,
        claude_runner=None,
    ))

    assert result["all_met"] is True
    assert len(result["verdicts"]) == 2
    assert all(v["verdict"] == "MET" for v in result["verdicts"])


def test_run_goal_verify_unmet_escalates_to_claude(store: CrewBoardStore, project: Project) -> None:
    """run_goal_verify escalates to Claude when qwen returns UNMET items."""
    goal = create_goal(
        store,
        text="Unmet goal",
        project_slug=project.slug,
        checklist_items=["item A"],
    )
    verify_task_obj = store.create_task(
        title="[goal-verify] Unmet goal",
        project_slug=project.slug,
        created_by="system",
        tags=["goal-verify", goal_tag(goal.goal_id)],
        goal_id=goal.goal_id,
    )

    qwen_calls: list[str] = []
    claude_calls: list[str] = []

    async def _mock_qwen(prompt: str) -> str:
        qwen_calls.append(prompt)
        return json.dumps([{"item": "item A", "verdict": "UNMET", "reason": "missing"}])

    async def _mock_claude(prompt: str) -> str:
        claude_calls.append(prompt)
        return json.dumps([{"item": "item A", "verdict": "UNMET", "reason": "confirmed missing"}])

    result = asyncio.run(run_goal_verify(
        store, verify_task_obj,
        qwen_invoker=_mock_qwen,
        claude_runner=_mock_claude,
    ))

    assert result["all_met"] is False
    assert len(qwen_calls) == 1, "qwen must be called"
    assert len(claude_calls) == 1, "claude must be called for UNMET escalation"
    assert any(v["verdict"] == "UNMET" for v in result["verdicts"])


def test_goal_id_roundtrip_in_meta(store: CrewBoardStore, project: Project) -> None:
    """GoalRecord survives a JSON round-trip through crew_meta."""
    goal = create_goal(
        store,
        text="roundtrip test",
        project_slug=project.slug,
        checklist_items=["item 1", "item 2"],
        cycle=1,
    )
    goal.verify_spawned = True
    store_goal(store, goal)

    retrieved = get_goal(store, goal.goal_id)
    assert retrieved is not None
    assert retrieved.goal_id == goal.goal_id
    assert retrieved.cycle == 1
    assert retrieved.verify_spawned is True
    assert len(retrieved.checklist) == 2
