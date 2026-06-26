"""Tests for board registry + scoping (P2 v-Next).

Auth pattern mirrors test_board_auth.py:
  - _install_crew_store() wires a real SQLite-backed store.
  - auth_headers fixture uses _BOARD_TOKEN so mutations pass auth.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.routes.board import _BOARD_TOKEN


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _install_crew_store(client: TestClient, tmp_path: Path) -> CrewBoardStore:
    """Wire a real (disk-backed) CrewBoardStore on the test app."""
    store = CrewBoardStore(tmp_path / "board_instances_test.db")
    # Register a project so task creation doesn't 400 on unknown project.
    store.upsert_project(
        Project(
            slug="test",
            path=str(tmp_path / "test"),
            name="Test Project",
            enabled=True,
            push_allowed=False,
            test_cmd=None,
        )
    )
    client.app.state.crew_store = store
    return store


def _lb(client: TestClient) -> TestClient:
    """A loopback-addressed client sharing the same app — board READ routes
    (/state, /stats) now require loopback OR a Bearer (audit H2)."""
    return TestClient(client.app, client=("127.0.0.1", 51777))


@pytest.fixture
def board_store(client: TestClient, tmp_path: Path) -> CrewBoardStore:
    return _install_crew_store(client, tmp_path)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Headers that pass board mutation auth (X-Board-Token)."""
    return {
        "content-type": "application/json",
        "x-board-token": _BOARD_TOKEN,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_back_compat_no_board_id(
    client: TestClient, board_store: CrewBoardStore,
) -> None:
    """GET /board/state with no ?board= param returns all tasks (back-compat)."""
    resp = _lb(client).get("/board/state")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data


def test_board_list_has_default(
    client: TestClient, board_store: CrewBoardStore,
) -> None:
    """GET /board/list returns at least the default board."""
    resp = client.get("/board/list")
    assert resp.status_code == 200
    boards = resp.json()
    assert isinstance(boards, list)
    ids = [b["board_id"] for b in boards]
    assert "default" in ids


def test_board_list_shape(
    client: TestClient, board_store: CrewBoardStore,
) -> None:
    """Each board entry has board_id, name, description, created_at fields."""
    resp = client.get("/board/list")
    assert resp.status_code == 200
    boards = resp.json()
    assert len(boards) >= 1
    for b in boards:
        assert "board_id" in b
        assert "name" in b
        assert "description" in b
        assert "created_at" in b


def test_create_board(
    client: TestClient, board_store: CrewBoardStore, auth_headers: dict,
) -> None:
    """POST /board/boards creates a new board and returns it."""
    resp = client.post(
        "/board/boards",
        json={"board_id": "beta", "name": "Beta", "description": "Beta board"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()
    assert data["board_id"] == "beta"
    assert data["name"] == "Beta"

    # It should now appear in /board/list.
    resp2 = client.get("/board/list")
    ids = [b["board_id"] for b in resp2.json()]
    assert "beta" in ids


def test_create_board_duplicate_returns_409(
    client: TestClient, board_store: CrewBoardStore, auth_headers: dict,
) -> None:
    """POST /board/boards with an existing board_id returns 409."""
    resp = client.post(
        "/board/boards",
        json={"board_id": "dup", "name": "Dup", "description": ""},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)

    resp2 = client.post(
        "/board/boards",
        json={"board_id": "dup", "name": "Dup Again", "description": ""},
        headers=auth_headers,
    )
    assert resp2.status_code == 409


def test_create_board_requires_auth(
    client: TestClient, board_store: CrewBoardStore,
) -> None:
    """POST /board/boards without a token returns 403."""
    resp = client.post(
        "/board/boards",
        json={"board_id": "noauth", "name": "No Auth", "description": ""},
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 403


def test_board_scoping(
    client: TestClient, board_store: CrewBoardStore, auth_headers: dict,
) -> None:
    """Tasks on 'alpha' board don't appear in 'default' board's state."""
    # Create a second board.
    resp = client.post(
        "/board/boards",
        json={"board_id": "alpha", "name": "Alpha", "description": "test"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)

    # Create a task on the alpha board.
    resp = client.post(
        "/board/tasks",
        json={"title": "Alpha task", "project_slug": "test", "board_id": "alpha"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)

    # Create a task on the default board.
    resp = client.post(
        "/board/tasks",
        json={"title": "Default task", "project_slug": "test", "board_id": "default"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)

    # Alpha board should only have its task.
    resp = _lb(client).get("/board/state?board=alpha")
    assert resp.status_code == 200
    alpha_tasks = resp.json()["tasks"]
    assert any(t["title"] == "Alpha task" for t in alpha_tasks)
    assert not any(t["title"] == "Default task" for t in alpha_tasks)

    # Default board should only have its task.
    resp = _lb(client).get("/board/state?board=default")
    assert resp.status_code == 200
    default_tasks = resp.json()["tasks"]
    assert any(t["title"] == "Default task" for t in default_tasks)
    assert not any(t["title"] == "Alpha task" for t in default_tasks)


def test_no_board_param_returns_all(
    client: TestClient, board_store: CrewBoardStore, auth_headers: dict,
) -> None:
    """GET /board/state with no ?board= returns tasks from ALL boards."""
    # Create two boards and one task on each.
    client.post(
        "/board/boards",
        json={"board_id": "x1", "name": "X1", "description": ""},
        headers=auth_headers,
    )
    client.post(
        "/board/boards",
        json={"board_id": "x2", "name": "X2", "description": ""},
        headers=auth_headers,
    )
    client.post(
        "/board/tasks",
        json={"title": "Task on X1", "project_slug": "test", "board_id": "x1"},
        headers=auth_headers,
    )
    client.post(
        "/board/tasks",
        json={"title": "Task on X2", "project_slug": "test", "board_id": "x2"},
        headers=auth_headers,
    )

    resp = _lb(client).get("/board/state")
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()["tasks"]]
    assert "Task on X1" in titles
    assert "Task on X2" in titles


def test_stats_scoping(
    client: TestClient, board_store: CrewBoardStore, auth_headers: dict,
) -> None:
    """GET /board/stats?board=<id> counts only tasks on that board."""
    # Create a board and add a task to it.
    client.post(
        "/board/boards",
        json={"board_id": "stats-test", "name": "Stats Test", "description": ""},
        headers=auth_headers,
    )
    client.post(
        "/board/tasks",
        json={"title": "Stats task", "project_slug": "test", "board_id": "stats-test"},
        headers=auth_headers,
    )

    resp = _lb(client).get("/board/stats?board=stats-test")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_status" in data
    # The stats-test board has 1 task.
    total = sum(data["by_status"].values())
    assert total >= 1

    # The default board (no extra tasks added) should have 0 tasks in this test.
    resp2 = _lb(client).get("/board/stats?board=default")
    assert resp2.status_code == 200
    data2 = resp2.json()
    total2 = sum(data2.get("by_status", {}).values())
    assert total2 == 0


def test_default_board_task_has_board_id(
    client: TestClient, board_store: CrewBoardStore, auth_headers: dict,
) -> None:
    """A task created without explicit board_id gets board_id='default'."""
    resp = client.post(
        "/board/tasks",
        json={"title": "Implicit default", "project_slug": "test"},
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201)
    # Verify via scoped state that it lands in 'default'.
    resp2 = _lb(client).get("/board/state?board=default")
    titles = [t["title"] for t in resp2.json()["tasks"]]
    assert "Implicit default" in titles
