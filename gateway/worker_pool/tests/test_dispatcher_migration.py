"""Migration test for Dispatcher — schema must be created from scratch."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gateway.worker_pool.dispatcher import Dispatcher


def test_open_creates_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "hive_jobs.db"
    Dispatcher.open(db_path)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()

    assert "hive_jobs" in tables


def test_open_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "hive_jobs.db"
    Dispatcher.open(db_path)
    Dispatcher.open(db_path)  # must not raise


def test_columns_present(tmp_path: Path) -> None:
    db_path = tmp_path / "hive_jobs.db"
    Dispatcher.open(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(hive_jobs)")}
    finally:
        conn.close()
    expected = {
        "id", "kind", "payload_json", "required_caps_json", "status",
        "attempts", "max_attempts", "node_id", "result_json", "error",
        "duration_ms", "created", "dispatched_at", "completed_at",
    }
    assert expected.issubset(cols), f"missing: {expected - cols}"
