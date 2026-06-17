"""SQLite-backed node registry for the hive worker pool.

Tracks paired nodes, their latest capability snapshot, and a small
rolling heartbeat history for debugging. Source-of-truth lives at
`state/hive_nodes.db`. The DB is host-only — nodes never read it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_node_id() -> str:
    # ULID-ish without the dependency: timestamp + random.
    return "n_" + secrets.token_urlsafe(12)


@dataclass(frozen=True, slots=True)
class HiveNode:
    id: str
    name: str
    token_hash: str
    created: float
    last_seen: float
    revoked: bool
    agent_version: str
    capabilities_json: str
    labels: tuple[str, ...]


class NodeRegistry:
    """Thin SQLite wrapper. Thread-safe via a single mutex."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    @classmethod
    def open(cls, db_path: Path) -> "NodeRegistry":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL: heartbeats write every 15s per node; the admin UI polls
        # /v1/nodes every 5s. Default rollback-journal mode serialises
        # all reads behind those writes. WAL lets list/get queries run
        # concurrently with the heartbeat UPDATE.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        cls._apply_migration(conn)
        cls._apply_indexes(conn)
        return cls(conn)

    @staticmethod
    def _apply_migration(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS hive_schema (version INTEGER NOT NULL)"
        )
        cur = conn.execute("SELECT version FROM hive_schema").fetchone()
        current = int(cur["version"]) if cur else 0
        if current >= _SCHEMA_VERSION:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hive_nodes (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                token_hash      TEXT NOT NULL UNIQUE,
                audience_json   TEXT NOT NULL DEFAULT '["all"]',
                created         REAL NOT NULL,
                last_seen       REAL NOT NULL,
                revoked         INTEGER NOT NULL DEFAULT 0,
                agent_version   TEXT NOT NULL DEFAULT '',
                capabilities_json TEXT NOT NULL DEFAULT '{}',
                labels_json     TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS hive_node_heartbeats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id         TEXT NOT NULL,
                received_at     REAL NOT NULL,
                capabilities_json TEXT NOT NULL,
                FOREIGN KEY (node_id) REFERENCES hive_nodes(id)
            );

            CREATE INDEX IF NOT EXISTS hive_node_heartbeats_by_node
                ON hive_node_heartbeats(node_id, received_at DESC);
            """
        )
        conn.execute("DELETE FROM hive_schema")
        conn.execute("INSERT INTO hive_schema (version) VALUES (?)", (_SCHEMA_VERSION,))
        conn.commit()

    @staticmethod
    def _apply_indexes(conn: sqlite3.Connection) -> None:
        """Idempotent index creation, runs every open. Lets us add
        scheduling-relevant indexes without bumping schema version.
        """
        conn.executescript(
            """
            -- Phase 2 scheduler will repeatedly query "active nodes
            -- with a fresh heartbeat". Without this index that's a
            -- full scan on every dispatch tick.
            CREATE INDEX IF NOT EXISTS hive_nodes_active_lastseen
                ON hive_nodes(revoked, last_seen DESC);

            -- agent_version filter for staged rollouts.
            CREATE INDEX IF NOT EXISTS hive_nodes_agent_version
                ON hive_nodes(agent_version);
            """
        )
        conn.commit()

    def add(
        self,
        *,
        name: str,
        token: str,
        labels: tuple[str, ...] = (),
    ) -> HiveNode:
        node_id = _new_node_id()
        now = time.time()
        token_hash = _hash_token(token)
        with self._lock:
            # audience_json column retained for back-compat with v1 DBs; the
            # column DEFAULT '["all"]' fills it on INSERT. Phase 2 will drop
            # the column outright if it stays unused.
            self._conn.execute(
                """
                INSERT INTO hive_nodes
                  (id, name, token_hash, created, last_seen,
                   revoked, agent_version, capabilities_json, labels_json)
                VALUES (?, ?, ?, ?, ?, 0, '', '{}', ?)
                """,
                (
                    node_id, name, token_hash,
                    now, now,
                    json.dumps(list(labels)),
                ),
            )
            self._conn.commit()
        return self._row_to_node({
            "id": node_id, "name": name, "token_hash": token_hash,
            "created": now, "last_seen": now, "revoked": 0,
            "agent_version": "", "capabilities_json": "{}",
            "labels_json": json.dumps(list(labels)),
        })

    def get(self, node_id: str) -> HiveNode | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hive_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        return self._row_to_node(dict(row)) if row else None

    def list(self) -> list[HiveNode]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hive_nodes ORDER BY created"
            ).fetchall()
        return [self._row_to_node(dict(r)) for r in rows]

    def list_active(self) -> list[HiveNode]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hive_nodes WHERE revoked = 0 ORDER BY created"
            ).fetchall()
        return [self._row_to_node(dict(r)) for r in rows]

    def revoke(self, node_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE hive_nodes SET revoked = 1 WHERE id = ?", (node_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def purge(self, node_id: str) -> bool:
        with self._lock:
            self._conn.execute(
                "DELETE FROM hive_node_heartbeats WHERE node_id = ?", (node_id,)
            )
            cur = self._conn.execute(
                "DELETE FROM hive_nodes WHERE id = ?", (node_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def verify_token(self, token: str) -> HiveNode | None:
        candidate = _hash_token(token)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hive_nodes WHERE revoked = 0"
            ).fetchall()
        for row in rows:
            if hmac.compare_digest(row["token_hash"], candidate):
                return self._row_to_node(dict(row))
        return None

    def record_heartbeat(
        self, node_id: str, capabilities: dict[str, Any],
    ) -> None:
        now = time.time()
        caps_json = json.dumps(capabilities, sort_keys=True)
        agent_version = str(capabilities.get("agent_version", ""))
        labels = capabilities.get("labels") or []
        if not isinstance(labels, list):
            labels = []
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM hive_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if exists is None:
                raise ValueError(f"unknown node id: {node_id!r}")
            self._conn.execute(
                """
                UPDATE hive_nodes
                   SET last_seen = ?, capabilities_json = ?,
                       agent_version = ?, labels_json = ?
                 WHERE id = ?
                """,
                (now, caps_json, agent_version, json.dumps(labels), node_id),
            )
            self._conn.execute(
                """
                INSERT INTO hive_node_heartbeats
                  (node_id, received_at, capabilities_json)
                VALUES (?, ?, ?)
                """,
                (node_id, now, caps_json),
            )
            # Bound history to last 10 per node.
            self._conn.execute(
                """
                DELETE FROM hive_node_heartbeats
                 WHERE node_id = ?
                   AND id NOT IN (
                       SELECT id FROM hive_node_heartbeats
                        WHERE node_id = ?
                        ORDER BY received_at DESC LIMIT 10
                   )
                """,
                (node_id, node_id),
            )
            self._conn.commit()

    def recent_heartbeats(
        self, node_id: str, *, limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT capabilities_json FROM hive_node_heartbeats
                 WHERE node_id = ? ORDER BY received_at DESC LIMIT ?
                """,
                (node_id, limit),
            ).fetchall()
        return [json.loads(r["capabilities_json"]) for r in rows]

    @staticmethod
    def _row_to_node(row: dict[str, Any]) -> HiveNode:
        return HiveNode(
            id=str(row["id"]),
            name=str(row["name"]),
            token_hash=str(row["token_hash"]),
            created=float(row["created"]),
            last_seen=float(row["last_seen"]),
            revoked=bool(row["revoked"]),
            agent_version=str(row["agent_version"]),
            capabilities_json=str(row["capabilities_json"]),
            labels=tuple(json.loads(row["labels_json"])),
        )


def sweep_offline_nodes(
    *,
    registry: "NodeRegistry",
    dispatcher: Any,                # gateway.worker_pool.dispatcher.Dispatcher
    offline_after_s: int,
) -> tuple[int, int]:
    """For every node whose last_seen < (now - offline_after_s), call
    `dispatcher.requeue_orphaned(node_id=...)`. Returns total
    (n_requeued, n_failed) across all stale nodes.

    Pure function — does not mutate node rows themselves; the node row
    keeps showing in `list_active()` but its `status` field reads
    'offline' to the admin UI via the existing `_status` helper in
    `routes/nodes.py`.
    """
    cutoff = time.time() - offline_after_s
    total_requeued = 0
    total_failed = 0
    for node in registry.list_active():
        if node.last_seen >= cutoff:
            continue
        n_q, n_f = dispatcher.requeue_orphaned(node_id=node.id)
        total_requeued += n_q
        total_failed += n_f
    return total_requeued, total_failed
