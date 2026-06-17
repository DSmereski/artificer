"""Tests for CrewBoardStore.tokens_by_day aggregation.

Covers: grouping by day, hive/claude sums, zero-fill for inactive days,
day-count boundary, ascending date order, and the /board/tokens-by-day
HTTP endpoint.
"""

from __future__ import annotations

import datetime
import sqlite3

import pytest
from fastapi.testclient import TestClient

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board import schema


@pytest.fixture
def store(tmp_path) -> CrewBoardStore:
    return CrewBoardStore(tmp_path / "crew.db")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _seed_task_with_tokens(
    store: CrewBoardStore,
    *,
    title: str,
    project_slug: str,
    hive: int,
    claude: int,
    updated_at: str,          # 'YYYY-MM-DD HH:MM:SS' UTC
) -> str:
    """Create a task then directly patch its token counts + updated_at."""
    store.upsert_project(Project(slug=project_slug, path="/tmp/p", name=project_slug))
    t = store.create_task(title=title, project_slug=project_slug, created_by="owner")
    with store._lock:
        store._conn.execute(
            "UPDATE crew_tasks SET hive_tokens=?, claude_tokens=?, updated_at=? "
            "WHERE slug=?",
            (hive, claude, updated_at, t.slug),
        )
        store._conn.commit()
    return t.slug


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _days_ago(n: int) -> str:
    d = datetime.date.today() - datetime.timedelta(days=n)
    return d.isoformat()


# ─── aggregation tests ────────────────────────────────────────────────────────

def test_empty_store_returns_zero_filled(store):
    """With no tasks the result must still have exactly 30 entries, all zeros."""
    rows = store.tokens_by_day(days=30)
    assert len(rows) == 30
    for r in rows:
        assert r["hive"] == 0
        assert r["claude"] == 0
        assert r["total"] == 0


def test_ascending_date_order(store):
    rows = store.tokens_by_day(days=7)
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates), "dates must be in ascending order"


def test_day_count_matches_requested(store):
    for n in (1, 7, 14, 30):
        rows = store.tokens_by_day(days=n)
        assert len(rows) == n, f"expected {n} rows, got {len(rows)}"


def test_last_day_is_today(store):
    rows = store.tokens_by_day(days=30)
    assert rows[-1]["date"] == _today_iso()


def test_first_day_is_n_minus_1_days_ago(store):
    rows = store.tokens_by_day(days=10)
    expected = _days_ago(9)
    assert rows[0]["date"] == expected


def test_single_task_counted_on_correct_day(store):
    today = _today_iso()
    _seed_task_with_tokens(
        store, title="t1", project_slug="p",
        hive=1000, claude=500,
        updated_at=f"{today} 12:00:00",
    )
    rows = store.tokens_by_day(days=30)
    today_row = next(r for r in rows if r["date"] == today)
    assert today_row["hive"] == 1000
    assert today_row["claude"] == 500
    assert today_row["total"] == 1500


def test_multiple_tasks_same_day_are_summed(store):
    today = _today_iso()
    _seed_task_with_tokens(
        store, title="t1", project_slug="p",
        hive=200, claude=100,
        updated_at=f"{today} 09:00:00",
    )
    _seed_task_with_tokens(
        store, title="t2", project_slug="p",
        hive=300, claude=400,
        updated_at=f"{today} 17:00:00",
    )
    rows = store.tokens_by_day(days=30)
    today_row = next(r for r in rows if r["date"] == today)
    assert today_row["hive"] == 500
    assert today_row["claude"] == 500
    assert today_row["total"] == 1000


def test_tasks_on_different_days_stay_separate(store):
    today = _today_iso()
    yesterday = _days_ago(1)
    _seed_task_with_tokens(
        store, title="today-task", project_slug="p",
        hive=100, claude=50,
        updated_at=f"{today} 10:00:00",
    )
    _seed_task_with_tokens(
        store, title="yesterday-task", project_slug="p",
        hive=200, claude=80,
        updated_at=f"{yesterday} 10:00:00",
    )
    rows = store.tokens_by_day(days=7)
    by_date = {r["date"]: r for r in rows}
    assert by_date[today]["hive"] == 100
    assert by_date[today]["claude"] == 50
    assert by_date[yesterday]["hive"] == 200
    assert by_date[yesterday]["claude"] == 80


def test_zero_fill_for_inactive_days(store):
    """Days with no tasks must appear with zeros, not be absent."""
    today = _today_iso()
    # Only seed today — days 1..29 should be zero.
    _seed_task_with_tokens(
        store, title="today-only", project_slug="p",
        hive=50, claude=25,
        updated_at=f"{today} 00:01:00",
    )
    rows = store.tokens_by_day(days=30)
    zero_days = [r for r in rows if r["date"] != today]
    assert all(r["hive"] == 0 for r in zero_days)
    assert all(r["claude"] == 0 for r in zero_days)


def test_tasks_older_than_window_excluded(store):
    """Tasks updated before the window must not appear in totals."""
    very_old = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
    _seed_task_with_tokens(
        store, title="ancient", project_slug="p",
        hive=99999, claude=99999,
        updated_at=f"{very_old} 12:00:00",
    )
    rows = store.tokens_by_day(days=30)
    assert all(r["hive"] == 0 and r["claude"] == 0 for r in rows)


def test_total_field_equals_hive_plus_claude(store):
    today = _today_iso()
    _seed_task_with_tokens(
        store, title="mix", project_slug="p",
        hive=333, claude=777,
        updated_at=f"{today} 06:00:00",
    )
    rows = store.tokens_by_day(days=1)
    assert rows[0]["total"] == rows[0]["hive"] + rows[0]["claude"]


# ─── HTTP endpoint tests ──────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """Minimal FastAPI app with the board router mounted."""
    from fastapi import FastAPI
    from gateway.routes.board import router

    app = FastAPI()
    store = CrewBoardStore(tmp_path / "board.db")
    app.state.crew_store = store
    app.include_router(router)
    return TestClient(app), store


def test_endpoint_returns_200(client):
    tc, _ = client
    r = tc.get("/board/tokens-by-day")
    assert r.status_code == 200


def test_endpoint_default_30_days(client):
    tc, _ = client
    data = tc.get("/board/tokens-by-day").json()
    assert len(data) == 30


def test_endpoint_respects_days_param(client):
    tc, _ = client
    data = tc.get("/board/tokens-by-day?days=7").json()
    assert len(data) == 7


def test_endpoint_response_schema(client):
    tc, _ = client
    rows = tc.get("/board/tokens-by-day?days=3").json()
    for r in rows:
        assert "date" in r
        assert "hive" in r
        assert "claude" in r
        assert "total" in r
        assert isinstance(r["hive"], int)
        assert isinstance(r["claude"], int)
        assert r["total"] == r["hive"] + r["claude"]


def test_endpoint_no_auth_required(client):
    """Endpoint must be open — no X-Board-Token or Bearer needed."""
    tc, _ = client
    r = tc.get("/board/tokens-by-day")
    assert r.status_code == 200  # not 403


def test_endpoint_reflects_seeded_data(client):
    tc, store = client
    today = _today_iso()
    _seed_task_with_tokens(
        store, title="ep-test", project_slug="p",
        hive=1234, claude=567,
        updated_at=f"{today} 08:00:00",
    )
    rows = tc.get("/board/tokens-by-day?days=1").json()
    assert len(rows) == 1
    assert rows[0]["date"] == today
    assert rows[0]["hive"] == 1234
    assert rows[0]["claude"] == 567
    assert rows[0]["total"] == 1234 + 567
