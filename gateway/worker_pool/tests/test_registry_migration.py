"""Migration test for NodeRegistry — schema must be created from scratch."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gateway.worker_pool.registry import NodeRegistry


def test_open_creates_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "hive_nodes.db"
    NodeRegistry.open(db_path)

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

    assert "hive_nodes" in tables
    assert "hive_node_heartbeats" in tables


def test_open_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "hive_nodes.db"
    NodeRegistry.open(db_path)
    NodeRegistry.open(db_path)  # must not raise
