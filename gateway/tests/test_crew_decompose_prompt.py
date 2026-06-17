"""Tests for the strengthened /board/decompose prompt (item 4).

Verifies that decompose_goal creates tasks with:
  - acceptance_criteria populated (criteria field)
  - depends_on chains wired between tickets
  - files_of_interest hints present

The OllamaInvoker is patched so these tests run offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.routes.board import _BOARD_TOKEN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _install_crew_store(client: TestClient, tmp_path: Path) -> CrewBoardStore:
    store = CrewBoardStore(tmp_path / "decompose_test.db")
    store.upsert_project(
        Project(
            slug="test-proj",
            path=str(tmp_path / "test-proj"),
            name="Test Project",
            enabled=True,
            push_allowed=False,
            test_cmd="python -m pytest -q",
        )
    )
    client.app.state.crew_store = store
    return store


def _make_plan(tickets: list[dict]) -> dict:
    """Wrap tickets in the planner response envelope."""
    return {"project_name": "test-proj", "tickets": tickets}


def _fake_plan_response(plan: dict) -> tuple[str, int, int]:
    return json.dumps(plan), 100, 100


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_decompose_creates_tasks_with_criteria_and_depends_on(
    client: TestClient, tmp_path: Path
) -> None:
    """decompose_goal must create tickets with acceptance_criteria and a
    depends_on chain when the LLM returns a multi-step plan."""
    store = _install_crew_store(client, tmp_path)

    plan = _make_plan([
        {
            "title": "Create data model",
            "body": "Define the User dataclass with id, name, email fields.",
            "criteria": [
                "file src/models/user.py exists",
                "class User has fields id, name, email",
            ],
            "files": ["src/models/user.py"],
            "depends_on": [],
        },
        {
            "title": "Implement user service",
            "body": "Write UserService with create_user and get_user methods.",
            "criteria": [
                "UserService.create_user returns a User instance",
                "pytest passes: test_user_service.py all green",
            ],
            "files": ["src/services/user_service.py", "tests/test_user_service.py"],
            "depends_on": [0],
        },
        {
            "title": "Add REST endpoint",
            "body": "Wire a POST /users FastAPI route using UserService.",
            "criteria": [
                "POST /users returns 201 with the created user JSON",
                "GET /users/{id} returns 200 or 404",
            ],
            "files": ["src/routes/users.py"],
            "depends_on": [1],
        },
    ])

    with patch(
        "gateway.helpers.base.OllamaInvoker",
        return_value=AsyncMock(
            chat=AsyncMock(return_value=_fake_plan_response(plan))
        ),
    ):
        r = client.post(
            "/board/decompose",
            json={"goal": "build a user management API", "project_slug": "test-proj"},
            headers={"x-board-token": _BOARD_TOKEN},
        )

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] == 3

    tasks = store.list_tasks()
    assert len(tasks) == 3

    # Every task must have at least one acceptance criterion.
    for t in tasks:
        assert t.acceptance_criteria, (
            f"task {t.slug!r} has no acceptance_criteria"
        )

    # depends_on chains must be wired.
    by_title = {t.title: t for t in tasks}
    t0 = by_title["Create data model"]
    t1 = by_title["Implement user service"]
    t2 = by_title["Add REST endpoint"]

    assert t0.depends_on == [], f"t0 should have no deps, got {t0.depends_on!r}"
    assert t0.slug in t1.depends_on, (
        f"t1 should depend on t0 ({t0.slug!r}), got {t1.depends_on!r}"
    )
    assert t1.slug in t2.depends_on, (
        f"t2 should depend on t1 ({t1.slug!r}), got {t2.depends_on!r}"
    )


def test_decompose_falls_back_to_sequential_chain_when_depends_on_missing(
    client: TestClient, tmp_path: Path
) -> None:
    """When the LLM omits depends_on, the route falls back to the linear
    sequential chain (each ticket depends on the previous one)."""
    store = _install_crew_store(client, tmp_path)

    # Plan with no depends_on field (legacy / non-compliant LLM output)
    plan = _make_plan([
        {
            "title": "Step A",
            "body": "first step",
            "criteria": ["file a.py exists"],
            "files": ["a.py"],
        },
        {
            "title": "Step B",
            "body": "second step",
            "criteria": ["file b.py exists"],
            "files": ["b.py"],
        },
    ])

    with patch(
        "gateway.helpers.base.OllamaInvoker",
        return_value=AsyncMock(
            chat=AsyncMock(return_value=_fake_plan_response(plan))
        ),
    ):
        r = client.post(
            "/board/decompose",
            json={"goal": "two steps", "project_slug": "test-proj"},
            headers={"x-board-token": _BOARD_TOKEN},
        )

    assert r.status_code == 200, r.text
    tasks = store.list_tasks()
    assert len(tasks) == 2

    by_title = {t.title: t for t in tasks}
    step_a = by_title["Step A"]
    step_b = by_title["Step B"]

    # Sequential fallback: B depends on A.
    assert step_a.depends_on == []
    assert step_a.slug in step_b.depends_on


def test_decompose_includes_files_of_interest(
    client: TestClient, tmp_path: Path
) -> None:
    """files field from the LLM is stored as files_of_interest on the task."""
    _install_crew_store(client, tmp_path)

    plan = _make_plan([
        {
            "title": "Write model",
            "body": "define the model",
            "criteria": ["model.py exists"],
            "files": ["src/model.py", "tests/test_model.py"],
            "depends_on": [],
        },
    ])

    with patch(
        "gateway.helpers.base.OllamaInvoker",
        return_value=AsyncMock(
            chat=AsyncMock(return_value=_fake_plan_response(plan))
        ),
    ):
        r = client.post(
            "/board/decompose",
            json={"goal": "write model", "project_slug": "test-proj"},
            headers={"x-board-token": _BOARD_TOKEN},
        )

    assert r.status_code == 200, r.text
    store_ref = client.app.state.crew_store
    tasks = store_ref.list_tasks()
    assert len(tasks) == 1
    assert "src/model.py" in tasks[0].files_of_interest
    assert "tests/test_model.py" in tasks[0].files_of_interest
