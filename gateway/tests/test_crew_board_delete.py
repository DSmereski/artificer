"""Tests for the crew-board hard-delete feature.

Covers:
  - store.delete_task removes crew_tasks + crew_audit + crew_approvals +
    crew_lessons rows for the slug (no orphans left in child tables).
  - store.delete_task on a missing slug returns False.
  - DELETE /board/tasks/{slug} returns 404 when slug not found.
  - DELETE /board/tasks/{slug} returns 403 without auth token.
  - POST /board/tasks/{slug}/delete alias works identically.
  - DELETE /board/tasks/{slug} succeeds for an in_progress task and the
    dispatcher's get_task(slug) None-guard pattern is unaffected
    (get_task returns None after deletion — no crash).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project
from gateway.routes.board import _BOARD_TOKEN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, suffix: str = "delete_test.db") -> CrewBoardStore:
    store = CrewBoardStore(tmp_path / suffix)
    store.upsert_project(
        Project(
            slug="proj",
            path=str(tmp_path / "proj"),
            name="Test Project",
            enabled=True,
            push_allowed=False,
            test_cmd=None,
        )
    )
    return store


def _install(client: TestClient, store: CrewBoardStore) -> None:
    client.app.state.crew_store = store


def _auth_headers() -> dict[str, str]:
    return {"x-board-token": _BOARD_TOKEN, "content-type": "application/json"}


# ---------------------------------------------------------------------------
# Store-level tests (no HTTP, pure SQLite)
# ---------------------------------------------------------------------------


def test_delete_task_removes_task_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t = store.create_task(title="To delete", project_slug="proj")
    assert store.get_task(t.slug) is not None

    result = store.delete_task(t.slug)

    assert result is True
    assert store.get_task(t.slug) is None


def test_delete_task_removes_audit_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t = store.create_task(title="Audited task", project_slug="proj")
    store.add_comment(t.slug, actor="owner", comment="first note")
    store.add_comment(t.slug, actor="hive", comment="second note")
    # Confirm rows exist before delete.
    assert len(store.audit_for(t.slug)) >= 3  # create + 2 comments

    store.delete_task(t.slug)

    # No audit rows survive the hard delete.
    rows = store._conn.execute(
        "SELECT COUNT(*) AS n FROM crew_audit WHERE task_slug = ?", (t.slug,)
    ).fetchone()
    assert rows["n"] == 0, "audit rows must be hard-deleted with the task"


def test_delete_task_removes_approval_rows(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t = store.create_task(title="Approval task", project_slug="proj")
    store.request_approval(
        task_slug=t.slug,
        requested_by="hive",
        kind="push",
        summary="push to main?",
    )
    rows_before = store._conn.execute(
        "SELECT COUNT(*) AS n FROM crew_approvals WHERE task_slug = ?", (t.slug,)
    ).fetchone()
    assert rows_before["n"] == 1

    store.delete_task(t.slug)

    rows_after = store._conn.execute(
        "SELECT COUNT(*) AS n FROM crew_approvals WHERE task_slug = ?", (t.slug,)
    ).fetchone()
    assert rows_after["n"] == 0, "approval rows must be hard-deleted with the task"


def test_delete_task_removes_lesson_rows_for_task(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t = store.create_task(title="Lesson task", project_slug="proj")
    store.add_lesson("proj", "remember this", task_slug=t.slug)
    rows_before = store._conn.execute(
        "SELECT COUNT(*) AS n FROM crew_lessons WHERE task_slug = ?", (t.slug,)
    ).fetchone()
    assert rows_before["n"] == 1

    store.delete_task(t.slug)

    rows_after = store._conn.execute(
        "SELECT COUNT(*) AS n FROM crew_lessons WHERE task_slug = ?", (t.slug,)
    ).fetchone()
    assert rows_after["n"] == 0, "lesson rows keyed to the task must be hard-deleted"


def test_delete_task_unknown_slug_returns_false(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    result = store.delete_task("T-9999")
    assert result is False


def test_delete_task_does_not_touch_other_tasks(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    keep = store.create_task(title="Keep me", project_slug="proj")
    gone = store.create_task(title="Delete me", project_slug="proj")
    store.add_comment(gone.slug, "system", "note")

    store.delete_task(gone.slug)

    # The other task and its audit trail are untouched.
    assert store.get_task(keep.slug) is not None
    listed = store.list_tasks()
    assert len(listed) == 1 and listed[0].slug == keep.slug


def test_delete_inprogress_task_succeeds_and_get_task_returns_none(
    tmp_path: Path,
) -> None:
    """Deleting an in_progress task is allowed.
    After deletion, get_task returns None — the dispatcher's
    'if task is None: bail' guard is naturally satisfied."""
    store = _make_store(tmp_path)
    # owner-created tasks land in backlog; drive to in_progress.
    t = store.create_task(title="In-flight job", project_slug="proj")
    store.move_task(t.slug, schema.STATUS_READY, actor="owner")
    store.move_task(t.slug, schema.STATUS_IN_PROGRESS, actor="owner")
    assert store.get_task(t.slug).status == schema.STATUS_IN_PROGRESS  # type: ignore[union-attr]

    result = store.delete_task(t.slug)

    assert result is True
    # Dispatcher guard: get_task returns None → no crash, loop bails.
    assert store.get_task(t.slug) is None


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


def test_delete_route_returns_404_for_missing_slug(
    client: TestClient, tmp_path: Path
) -> None:
    store = _make_store(tmp_path)
    _install(client, store)
    r = client.delete("/board/tasks/T-9999", headers=_auth_headers())
    assert r.status_code == 404, r.text


def test_delete_route_requires_auth(
    client: TestClient, tmp_path: Path
) -> None:
    store = _make_store(tmp_path)
    t = store.create_task(title="Protected", project_slug="proj")
    _install(client, store)
    r = client.delete(f"/board/tasks/{t.slug}")
    assert r.status_code == 403, r.text


def test_delete_route_removes_task_and_returns_slug(
    client: TestClient, tmp_path: Path
) -> None:
    store = _make_store(tmp_path)
    t = store.create_task(title="Real delete", project_slug="proj")
    store.add_comment(t.slug, "owner", "a note")
    _install(client, store)

    r = client.delete(f"/board/tasks/{t.slug}", headers=_auth_headers())

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": t.slug}
    assert store.get_task(t.slug) is None


def test_post_delete_alias_works(
    client: TestClient, tmp_path: Path
) -> None:
    """POST /board/tasks/{slug}/delete is the alias for clients that cannot
    send HTTP DELETE (e.g. the web UI fetch wrapper)."""
    store = _make_store(tmp_path)
    t = store.create_task(title="Alias delete", project_slug="proj")
    _install(client, store)

    r = client.post(
        f"/board/tasks/{t.slug}/delete", headers=_auth_headers(),
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": t.slug}
    assert store.get_task(t.slug) is None


def test_delete_route_bad_slug_format_returns_400(
    client: TestClient, tmp_path: Path
) -> None:
    store = _make_store(tmp_path)
    _install(client, store)
    r = client.delete("/board/tasks/not-a-slug", headers=_auth_headers())
    assert r.status_code == 400, r.text


def test_delete_inprogress_task_via_route_succeeds(
    client: TestClient, tmp_path: Path
) -> None:
    store = _make_store(tmp_path)
    # owner-created lands in backlog; drive to in_progress via state machine.
    t = store.create_task(title="Running job", project_slug="proj")
    store.move_task(t.slug, schema.STATUS_READY, actor="owner")
    store.move_task(t.slug, schema.STATUS_IN_PROGRESS, actor="owner")
    _install(client, store)

    r = client.delete(f"/board/tasks/{t.slug}", headers=_auth_headers())

    assert r.status_code == 200, r.text
    # get_task returns None — dispatcher guard is satisfied.
    assert store.get_task(t.slug) is None
