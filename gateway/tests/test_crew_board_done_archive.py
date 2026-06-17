"""Auto-archive of stale Done tasks (store.archive_old_done)."""

from __future__ import annotations

import pytest

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project


@pytest.fixture
def store(tmp_path) -> CrewBoardStore:
    s = CrewBoardStore(tmp_path / "crew.db")
    s.upsert_project(Project(slug="p", path=str(tmp_path / "p"), name="P"))
    return s


def _done(store: CrewBoardStore, title: str, *, age_days: float):
    t = store.create_task(title=title, project_slug="p")
    store._conn.execute(
        "UPDATE crew_tasks SET status=?, updated_at=datetime('now', ?) "
        "WHERE slug=?",
        (schema.STATUS_DONE, f"-{int(age_days * 86400)} seconds", t.slug),
    )
    store._conn.commit()
    return t.slug


def test_archives_done_older_than_retention(store):
    old = _done(store, "old", age_days=5)
    fresh = _done(store, "fresh", age_days=0)
    n = store.archive_old_done(3.0)
    assert n == 1
    assert store.get_task(old).status == schema.STATUS_ARCHIVED
    assert store.get_task(fresh).status == schema.STATUS_DONE


def test_nothing_due_returns_zero(store):
    _done(store, "fresh", age_days=1)
    assert store.archive_old_done(3.0) == 0


def test_retention_zero_disables(store):
    _done(store, "ancient", age_days=99)
    assert store.archive_old_done(0) == 0
    # and negative is also a no-op
    assert store.archive_old_done(-1) == 0


def test_only_done_is_swept(store):
    """A backlog task older than retention is NOT archived (only done)."""
    t = store.create_task(title="old backlog", project_slug="p")
    store._conn.execute(
        "UPDATE crew_tasks SET status=?, updated_at=datetime('now','-30 days') "
        "WHERE slug=?",
        (schema.STATUS_BACKLOG, t.slug),
    )
    store._conn.commit()
    assert store.archive_old_done(3.0) == 0
    assert store.get_task(t.slug).status == schema.STATUS_BACKLOG
