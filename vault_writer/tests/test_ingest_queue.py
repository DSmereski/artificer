"""Tests for vault_writer.ingest_queue (C1 — crash-safe ingest queue).

Three scenarios:
  (a) happy path: enqueue → drain → row 'done' + note indexed
  (b) crash recovery: stuck 'processing' row → recover_stuck → 'pending' → completes
  (c) poison item: process_fn always raises → max attempts → state='failed',
      queue keeps draining other items (not blocked)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio  # noqa: F401

from vault_writer.index import VaultIndex
from vault_writer.ingest_queue import (
    MAX_ATTEMPTS,
    drain,
    enqueue,
    ensure_schema,
    recover_stuck,
)


# ---------------------------------------------------------------- helpers


def _open_index(tmp_path: Path) -> VaultIndex:
    """Open a VaultIndex (which also creates ingest_queue via ensure_schema)."""
    return VaultIndex.open(tmp_path / "vault.db", dimension=8)


def _row(conn: sqlite3.Connection, row_id: int) -> dict | None:
    r = conn.execute(
        "SELECT id, state, attempts, error FROM ingest_queue WHERE id = ?",
        (row_id,),
    ).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "state": r["state"], "attempts": r["attempts"], "error": r["error"]}


def _pending_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM ingest_queue WHERE state = 'pending'"
        ).fetchone()[0]
    )


def _all_states(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT state FROM ingest_queue ORDER BY id").fetchall()
    return [r["state"] for r in rows]


# ---------------------------------------------------------------- (a) happy path


@pytest.mark.asyncio
async def test_enqueue_drain_done_and_indexed(tmp_path: Path) -> None:
    """Enqueue a note payload, drain it, assert state='done' and note indexed."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    # Record of calls to our fake process_fn
    indexed: list[dict] = []

    async def fake_process(payload: dict) -> None:
        # Simulate embed+index by upserting directly
        idx.upsert(
            path=f"knowledge/{payload['title'].replace(' ', '-')}.md",
            note_type="knowledge",
            author=payload["author"],
            audience=payload.get("audience", ["all"]),
            frontmatter={"title": payload["title"]},
            body=payload["body"],
            embedding=[0.1] * 8,
        )
        indexed.append(payload)

    note_payload = {
        "category": "knowledge",
        "title": "test note",
        "body": "This is a test note body.",
        "author": "claude-code",
        "audience": ["all"],
        "tags": [],
        "extra": {},
    }
    row_id = enqueue(conn, note_payload)

    # Verify the row is pending before drain
    assert _row(conn, row_id)["state"] == "pending"
    assert idx.count() == 0

    done = await drain(conn, fake_process)

    assert done == 1
    assert _row(conn, row_id)["state"] == "done"
    assert len(indexed) == 1
    assert indexed[0]["title"] == "test note"
    # Note was indexed
    assert idx.count() == 1

    idx.close()


# ---------------------------------------------------------------- (b) crash recovery


@pytest.mark.asyncio
async def test_crash_recovery_resets_processing_then_completes(tmp_path: Path) -> None:
    """Simulate a crash: manually set a row to 'processing', call recover_stuck,
    assert it reverts to 'pending', then drain completes it successfully."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    completed: list[str] = []

    async def fake_process(payload: dict) -> None:
        completed.append(payload["title"])

    # Enqueue and immediately force-set to 'processing' (simulates crash mid-drain)
    payload = {
        "category": "knowledge",
        "title": "crash note",
        "body": "survived a crash",
        "author": "claude-code",
        "audience": ["all"],
        "tags": [],
        "extra": {},
    }
    row_id = enqueue(conn, payload)

    # Simulate the daemon having died after marking it 'processing'
    conn.execute(
        "UPDATE ingest_queue SET state = 'processing' WHERE id = ?", (row_id,)
    )
    conn.commit()
    assert _row(conn, row_id)["state"] == "processing"

    # Startup crash recovery
    recovered = recover_stuck(conn)
    assert recovered == 1
    assert _row(conn, row_id)["state"] == "pending"

    # Drain completes it
    done = await drain(conn, fake_process)
    assert done == 1
    assert _row(conn, row_id)["state"] == "done"
    assert "crash note" in completed

    idx.close()


@pytest.mark.asyncio
async def test_recover_stuck_only_touches_processing(tmp_path: Path) -> None:
    """recover_stuck must leave 'done' and 'failed' rows undisturbed."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    async def noop(payload: dict) -> None:
        pass

    # Create one row in each steady state
    pending_id = enqueue(conn, {"title": "pending"})
    done_id = enqueue(conn, {"title": "done"})
    failed_id = enqueue(conn, {"title": "failed"})
    stuck_id = enqueue(conn, {"title": "stuck"})

    conn.execute("UPDATE ingest_queue SET state='done' WHERE id=?", (done_id,))
    conn.execute("UPDATE ingest_queue SET state='failed' WHERE id=?", (failed_id,))
    conn.execute("UPDATE ingest_queue SET state='processing' WHERE id=?", (stuck_id,))
    conn.commit()

    n = recover_stuck(conn)
    assert n == 1  # only the 'processing' one was reset

    assert _row(conn, pending_id)["state"] == "pending"
    assert _row(conn, done_id)["state"] == "done"
    assert _row(conn, failed_id)["state"] == "failed"
    assert _row(conn, stuck_id)["state"] == "pending"  # was 'processing', now reset

    idx.close()


# ---------------------------------------------------------------- (c) poison item


@pytest.mark.asyncio
async def test_poison_item_reaches_failed_after_max_attempts(tmp_path: Path) -> None:
    """A process_fn that always raises: after MAX_ATTEMPTS the row is 'failed'
    and subsequent drain calls on other items succeed (queue not blocked)."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    processed_good: list[str] = []

    async def sometimes_fails(payload: dict) -> None:
        if payload.get("title") == "poison":
            raise ValueError("always broken")
        processed_good.append(payload["title"])

    poison_id = enqueue(conn, {"title": "poison", "body": "bad", "author": "test",
                                "category": "knowledge", "audience": ["all"],
                                "tags": [], "extra": {}})
    good_id = enqueue(conn, {"title": "good", "body": "fine", "author": "test",
                              "category": "knowledge", "audience": ["all"],
                              "tags": [], "extra": {}})

    # Drain MAX_ATTEMPTS times; after that poison should be 'failed'
    for attempt_num in range(1, MAX_ATTEMPTS + 1):
        await drain(conn, sometimes_fails, max_attempts=MAX_ATTEMPTS, batch_size=20)
        poison_row = _row(conn, poison_id)
        if attempt_num < MAX_ATTEMPTS:
            assert poison_row["state"] == "pending", (
                f"attempt {attempt_num}: expected pending, got {poison_row['state']}"
            )
            assert poison_row["attempts"] == attempt_num
        else:
            assert poison_row["state"] == "failed"
            assert poison_row["attempts"] == MAX_ATTEMPTS
            assert "always broken" in (poison_row["error"] or "")

    # Good item was processed on the first drain (batch processes both)
    assert "good" in processed_good

    # Good item state is 'done'
    assert _row(conn, good_id)["state"] == "done"

    # Queue is not stuck — further drains run without error
    extra_id = enqueue(conn, {"title": "extra", "body": "ok", "author": "test",
                               "category": "knowledge", "audience": ["all"],
                               "tags": [], "extra": {}})
    done = await drain(conn, sometimes_fails)
    assert done == 1
    assert _row(conn, extra_id)["state"] == "done"

    idx.close()


@pytest.mark.asyncio
async def test_poison_error_text_stored(tmp_path: Path) -> None:
    """The error column must capture the exception message for diagnostics."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    async def always_raises(payload: dict) -> None:
        raise RuntimeError("unique diagnostic message")

    row_id = enqueue(conn, {"title": "bad", "body": "x", "author": "test",
                             "category": "knowledge", "audience": ["all"],
                             "tags": [], "extra": {}})

    for _ in range(MAX_ATTEMPTS):
        await drain(conn, always_raises, max_attempts=MAX_ATTEMPTS)

    row = _row(conn, row_id)
    assert row["state"] == "failed"
    assert "unique diagnostic message" in (row["error"] or "")

    idx.close()


# ---------------------------------------------------------------- schema tests


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    """Calling ensure_schema twice on the same DB must not raise."""
    db = tmp_path / "vault.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    ensure_schema(conn)  # second call — must be silent
    # Table exists with the right columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ingest_queue)").fetchall()}
    for expected in ("id", "payload", "state", "attempts", "error",
                     "created_at", "updated_at"):
        assert expected in cols, f"column {expected!r} missing"
    conn.close()


def test_vault_index_open_creates_ingest_queue(tmp_path: Path) -> None:
    """VaultIndex.open() must create the ingest_queue table automatically."""
    idx = VaultIndex.open(tmp_path / "v.db", dimension=8)
    try:
        tables = {
            r[0] for r in idx._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "ingest_queue" in tables
    finally:
        idx.close()


def test_enqueue_returns_increasing_ids(tmp_path: Path) -> None:
    """Row IDs from successive enqueues must be strictly increasing."""
    idx = _open_index(tmp_path)
    conn = idx._conn
    payload = {"title": "x", "body": "y", "author": "test",
               "category": "knowledge", "audience": ["all"],
               "tags": [], "extra": {}}
    ids = [enqueue(conn, payload) for _ in range(5)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 5
    idx.close()


def test_enqueue_stores_payload_as_json(tmp_path: Path) -> None:
    """The stored payload must round-trip through JSON cleanly."""
    idx = _open_index(tmp_path)
    conn = idx._conn
    payload = {"category": "ops", "title": "my pref", "body": "value",
               "author": "claude-code", "audience": ["claude-code"],
               "tags": ["pref"], "extra": {"k": "v"}}
    row_id = enqueue(conn, payload)
    stored = conn.execute(
        "SELECT payload FROM ingest_queue WHERE id = ?", (row_id,)
    ).fetchone()
    assert json.loads(stored["payload"]) == payload
    idx.close()


@pytest.mark.asyncio
async def test_drain_empty_queue_returns_zero(tmp_path: Path) -> None:
    """drain() on an empty queue must return 0 and not raise."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    async def noop(payload: dict) -> None:
        pass

    done = await drain(conn, noop)
    assert done == 0
    idx.close()


@pytest.mark.asyncio
async def test_drain_honours_batch_size(tmp_path: Path) -> None:
    """With batch_size=2 and 5 items, first drain processes 2, not 5."""
    idx = _open_index(tmp_path)
    conn = idx._conn

    processed: list[str] = []

    async def record(payload: dict) -> None:
        processed.append(payload["title"])

    payload_tmpl = {"body": "x", "author": "test", "category": "knowledge",
                    "audience": ["all"], "tags": [], "extra": {}}
    for i in range(5):
        enqueue(conn, {**payload_tmpl, "title": f"item-{i}"})

    done = await drain(conn, record, batch_size=2)
    assert done == 2
    assert len(processed) == 2
    # Remaining 3 still pending
    assert _pending_count(conn) == 3

    idx.close()
