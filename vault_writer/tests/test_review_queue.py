"""Tests for vault_writer.review_queue (C4).

Covers:
  (a) add_review inserts a row with correct fields.
  (b) get_open_reviews returns only open items, newest first.
  (c) resolve_review transitions to resolved/dismissed; returns False if not found.
  (d) count_open returns correct count.
  (e) ensure_schema is idempotent.
"""

from __future__ import annotations

import sqlite3

import pytest

from vault_writer.review_queue import (
    add_review,
    count_open,
    ensure_schema,
    get_open_reviews,
    resolve_review,
)


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory SQLite connection with the wiki_reviews schema."""
    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    return db


# ------------------------------------------------------------------ (a) add_review


def test_add_review_returns_positive_id(conn: sqlite3.Connection) -> None:
    row_id = add_review(conn, slug="hive-config", kind="gap", summary="Port not documented")
    assert isinstance(row_id, int)
    assert row_id > 0


def test_add_review_fields_stored(conn: sqlite3.Connection) -> None:
    add_review(
        conn,
        slug="hive-gateway",
        kind="contradiction",
        summary="Port 9000 contradicts existing 8765.",
        source_notes=["ops/config.md"],
    )
    rows = conn.execute("SELECT slug, kind, summary, source_notes, status FROM wiki_reviews").fetchall()
    assert len(rows) == 1
    slug, kind, summary, source_notes_raw, status = rows[0]
    assert slug == "hive-gateway"
    assert kind == "contradiction"
    assert "9000" in summary
    assert "ops/config.md" in source_notes_raw
    assert status == "open"


def test_add_review_source_notes_default_empty(conn: sqlite3.Connection) -> None:
    add_review(conn, slug="sc-economy", kind="gap", summary="Economy gap")
    rows = conn.execute("SELECT source_notes FROM wiki_reviews").fetchall()
    assert rows[0][0] == "[]"


# ------------------------------------------------------------------ (b) get_open_reviews


def test_get_open_reviews_returns_open_only(conn: sqlite3.Connection) -> None:
    id1 = add_review(conn, slug="a", kind="gap", summary="Gap A")
    id2 = add_review(conn, slug="b", kind="contradiction", summary="Contradiction B")
    resolve_review(conn, id1, status="resolved")

    items = get_open_reviews(conn)
    assert len(items) == 1
    assert items[0]["id"] == id2
    assert items[0]["status"] == "open"


def test_get_open_reviews_newest_first(conn: sqlite3.Connection) -> None:
    id1 = add_review(conn, slug="first", kind="gap", summary="First")
    id2 = add_review(conn, slug="second", kind="gap", summary="Second")

    items = get_open_reviews(conn)
    # newest first means id2 should come before id1
    assert items[0]["id"] == id2
    assert items[1]["id"] == id1


def test_get_open_reviews_limit(conn: sqlite3.Connection) -> None:
    for i in range(10):
        add_review(conn, slug=f"slug-{i}", kind="gap", summary=f"Gap {i}")

    items = get_open_reviews(conn, limit=3)
    assert len(items) == 3


def test_get_open_reviews_empty(conn: sqlite3.Connection) -> None:
    assert get_open_reviews(conn) == []


def test_get_open_reviews_fields(conn: sqlite3.Connection) -> None:
    add_review(conn, slug="x", kind="gap", summary="Missing X", source_notes=["file.md"])
    items = get_open_reviews(conn)
    item = items[0]
    assert set(item.keys()) >= {"id", "slug", "kind", "summary", "source_notes", "status", "created_at"}
    assert item["source_notes"] == ["file.md"]


# ------------------------------------------------------------------ (c) resolve_review


def test_resolve_review_marks_resolved(conn: sqlite3.Connection) -> None:
    row_id = add_review(conn, slug="x", kind="gap", summary="Gap")
    ok = resolve_review(conn, row_id, status="resolved")
    assert ok is True

    row = conn.execute(
        "SELECT status, resolved_at FROM wiki_reviews WHERE id = ?", (row_id,)
    ).fetchone()
    assert row[0] == "resolved"
    assert row[1] is not None  # resolved_at was set


def test_resolve_review_marks_dismissed(conn: sqlite3.Connection) -> None:
    row_id = add_review(conn, slug="x", kind="gap", summary="Gap")
    ok = resolve_review(conn, row_id, status="dismissed")
    assert ok is True
    row = conn.execute("SELECT status FROM wiki_reviews WHERE id = ?", (row_id,)).fetchone()
    assert row[0] == "dismissed"


def test_resolve_review_returns_false_for_missing(conn: sqlite3.Connection) -> None:
    ok = resolve_review(conn, 9999, status="resolved")
    assert ok is False


def test_resolve_review_returns_false_for_already_resolved(conn: sqlite3.Connection) -> None:
    row_id = add_review(conn, slug="x", kind="gap", summary="Gap")
    resolve_review(conn, row_id, status="resolved")
    # Second resolve should return False (already resolved, not open)
    ok = resolve_review(conn, row_id, status="resolved")
    assert ok is False


def test_resolve_review_invalid_status_raises(conn: sqlite3.Connection) -> None:
    row_id = add_review(conn, slug="x", kind="gap", summary="Gap")
    with pytest.raises(ValueError, match="invalid status"):
        resolve_review(conn, row_id, status="deleted")


# ------------------------------------------------------------------ (d) count_open


def test_count_open_zero(conn: sqlite3.Connection) -> None:
    assert count_open(conn) == 0


def test_count_open_after_adds(conn: sqlite3.Connection) -> None:
    add_review(conn, slug="a", kind="gap", summary="A")
    add_review(conn, slug="b", kind="gap", summary="B")
    assert count_open(conn) == 2


def test_count_open_after_resolve(conn: sqlite3.Connection) -> None:
    id1 = add_review(conn, slug="a", kind="gap", summary="A")
    add_review(conn, slug="b", kind="gap", summary="B")
    resolve_review(conn, id1)
    assert count_open(conn) == 1


# ------------------------------------------------------------------ (e) ensure_schema idempotent


def test_ensure_schema_idempotent() -> None:
    """Calling ensure_schema twice must not raise."""
    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    ensure_schema(db)  # must not raise
    db.close()
