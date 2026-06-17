"""SQLite-backed job queue for the hive worker pool.

Owns the `hive_jobs` table at `state/hive_jobs.db`. Mirrors the
`NodeRegistry` style: a single `sqlite3.Connection` guarded by a mutex,
schema migrations applied on `open()`. Long-poll waiters are kept in a
process-local in-memory dict — they're transient (die on restart, all
work re-queues from SQLite anyway).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _new_job_id() -> str:
    return "j_" + secrets.token_urlsafe(12)


_SCHEMA_VERSION = 1


# Job status string constants. Use these instead of bare strings.
STATUS_QUEUED = "queued"
STATUS_DISPATCHED = "dispatched"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HiveJob:
    id: str
    kind: str
    payload: dict[str, Any]
    required_caps: tuple[str, ...]
    status: str
    attempts: int
    max_attempts: int
    node_id: str | None
    result: dict[str, Any] | None
    error: str | None
    duration_ms: int | None
    created: float
    dispatched_at: float | None
    completed_at: float | None


class Dispatcher:
    """Thin SQLite wrapper. Thread-safe via a single mutex."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        # job_id -> asyncio.Future[HiveJob] — completion notifications for
        # `dispatch_and_wait`. Populated by `register_waiter`, resolved by
        # `complete` / `fail` / `requeue_orphaned`.
        self._waiters: dict[str, asyncio.Future[HiveJob]] = {}
        self._waiter_lock = threading.Lock()

    @classmethod
    def open(cls, db_path: Path) -> "Dispatcher":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cls._apply_migration(conn)
        return cls(conn)

    @staticmethod
    def _apply_migration(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS hive_job_schema (version INTEGER NOT NULL)"
        )
        cur = conn.execute("SELECT version FROM hive_job_schema").fetchone()
        current = int(cur["version"]) if cur else 0
        if current >= _SCHEMA_VERSION:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hive_jobs (
                id                  TEXT PRIMARY KEY,
                kind                TEXT NOT NULL,
                payload_json        TEXT NOT NULL,
                required_caps_json  TEXT NOT NULL DEFAULT '[]',
                status              TEXT NOT NULL DEFAULT 'queued',
                attempts            INTEGER NOT NULL DEFAULT 0,
                max_attempts        INTEGER NOT NULL DEFAULT 3,
                node_id             TEXT,
                result_json         TEXT,
                error               TEXT,
                duration_ms         INTEGER,
                created             REAL NOT NULL,
                dispatched_at       REAL,
                completed_at        REAL
            );

            CREATE INDEX IF NOT EXISTS hive_jobs_by_status_created
                ON hive_jobs(status, created);
            CREATE INDEX IF NOT EXISTS hive_jobs_by_node_status
                ON hive_jobs(node_id, status);
            """
        )
        conn.execute("DELETE FROM hive_job_schema")
        conn.execute(
            "INSERT INTO hive_job_schema (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        conn.commit()

    def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        required_caps: tuple[str, ...] = (),
        max_attempts: int = 3,
    ) -> HiveJob:
        job_id = _new_job_id()
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO hive_jobs
                  (id, kind, payload_json, required_caps_json, status,
                   attempts, max_attempts, node_id, result_json, error,
                   duration_ms, created, dispatched_at, completed_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL)
                """,
                (
                    job_id, kind,
                    json.dumps(payload),
                    json.dumps(list(required_caps)),
                    STATUS_QUEUED,
                    int(max_attempts),
                    now,
                ),
            )
            self._conn.commit()
        return HiveJob(
            id=job_id, kind=kind, payload=payload,
            required_caps=tuple(required_caps),
            status=STATUS_QUEUED, attempts=0, max_attempts=int(max_attempts),
            node_id=None, result=None, error=None, duration_ms=None,
            created=now, dispatched_at=None, completed_at=None,
        )

    def get(self, job_id: str) -> HiveJob | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hive_jobs WHERE id = ?", (job_id,),
            ).fetchone()
        return self._row_to_job(dict(row)) if row else None

    def get_queued(self) -> list[HiveJob]:
        """Oldest-first list of jobs in `queued` state."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM hive_jobs
                 WHERE status = ?
                 ORDER BY created ASC
                """,
                (STATUS_QUEUED,),
            ).fetchall()
        return [self._row_to_job(dict(r)) for r in rows]

    @staticmethod
    def _row_to_job(row: dict[str, Any]) -> HiveJob:
        return HiveJob(
            id=str(row["id"]),
            kind=str(row["kind"]),
            payload=json.loads(row["payload_json"]),
            required_caps=tuple(json.loads(row["required_caps_json"])),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            node_id=(str(row["node_id"]) if row["node_id"] else None),
            result=(
                json.loads(row["result_json"]) if row["result_json"] else None
            ),
            error=(str(row["error"]) if row["error"] else None),
            duration_ms=(
                int(row["duration_ms"]) if row["duration_ms"] is not None else None
            ),
            created=float(row["created"]),
            dispatched_at=(
                float(row["dispatched_at"]) if row["dispatched_at"] else None
            ),
            completed_at=(
                float(row["completed_at"]) if row["completed_at"] else None
            ),
        )

    def assign_to_node(self, job_id: str, *, node_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE hive_jobs
                   SET status = ?, node_id = ?, dispatched_at = ?,
                       attempts = attempts + 1
                 WHERE id = ? AND status = ?
                """,
                (STATUS_DISPATCHED, node_id, time.time(), job_id, STATUS_QUEUED),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def complete(
        self,
        job_id: str,
        *,
        result: dict[str, Any],
        duration_ms: int,
        node_id: str,
    ) -> bool:
        """Successful completion (status='done'). Wakes any waiter.

        Only updates the row when id, status=dispatched, AND node_id all
        match — defense-in-depth so a node cannot complete another node's job
        even if the route-layer check were bypassed.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE hive_jobs
                   SET status = ?, result_json = ?,
                       duration_ms = ?, completed_at = ?
                 WHERE id = ? AND status = ? AND node_id = ?
                """,
                (
                    STATUS_DONE, json.dumps(result),
                    int(duration_ms), time.time(),
                    job_id, STATUS_DISPATCHED, node_id,
                ),
            )
            self._conn.commit()
            ok = cur.rowcount > 0
        if ok:
            self._notify_waiter(job_id)
        return ok

    def report_adapter_error(
        self,
        job_id: str,
        *,
        error: str,
        duration_ms: int,
        node_id: str,
    ) -> bool:
        """Adapter ran but returned status='error'. Terminal — does NOT
        retry; the runtime says the work itself can't be done. Wakes
        any waiter.

        Only updates the row when id, status=dispatched, AND node_id all
        match.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE hive_jobs
                   SET status = ?, error = ?,
                       duration_ms = ?, completed_at = ?
                 WHERE id = ? AND status = ? AND node_id = ?
                """,
                (
                    STATUS_ERROR, str(error)[:1000],
                    int(duration_ms), time.time(),
                    job_id, STATUS_DISPATCHED, node_id,
                ),
            )
            self._conn.commit()
            ok = cur.rowcount > 0
        if ok:
            self._notify_waiter(job_id)
        return ok

    def fail(self, job_id: str, *, error: str) -> bool:
        """Infrastructure failure (timeout, node disappeared). Re-queues
        if attempts < max_attempts, else marks 'failed' (terminal,
        wakes waiter). Returns True iff terminal.

        Only acts on rows with status=STATUS_DISPATCHED. Calling fail()
        on a done/error/failed/queued job is a no-op (returns False) so
        a heartbeat-miss sweep racing a completed job cannot corrupt state.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts, max_attempts FROM hive_jobs WHERE id = ? AND status = ?",
                (job_id, STATUS_DISPATCHED),
            ).fetchone()
            if row is None:
                # Job does not exist OR is not in dispatched state — no-op.
                return False
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            terminal = attempts >= max_attempts
            if terminal:
                cur = self._conn.execute(
                    """
                    UPDATE hive_jobs
                       SET status = ?, error = ?, completed_at = ?
                     WHERE id = ? AND status = ?
                    """,
                    (
                        STATUS_FAILED, str(error)[:1000],
                        time.time(), job_id, STATUS_DISPATCHED,
                    ),
                )
            else:
                cur = self._conn.execute(
                    """
                    UPDATE hive_jobs
                       SET status = ?, error = ?, node_id = NULL,
                           dispatched_at = NULL
                     WHERE id = ? AND status = ?
                    """,
                    (STATUS_QUEUED, str(error)[:1000], job_id, STATUS_DISPATCHED),
                )
            terminal = cur.rowcount > 0 and terminal
            self._conn.commit()
        if terminal:
            self._notify_waiter(job_id)
        return terminal

    def requeue_orphaned(self, *, node_id: str) -> tuple[int, int]:
        """Recover jobs left dispatched against a vanished node.

        Returns (n_requeued, n_failed). Jobs whose `attempts >=
        max_attempts` go terminal (`failed`) and their waiter is
        notified; jobs under the cap return to `queued`.
        """
        n_requeued = 0
        n_failed: list[str] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, attempts, max_attempts FROM hive_jobs
                 WHERE node_id = ? AND status = ?
                """,
                (node_id, STATUS_DISPATCHED),
            ).fetchall()
            for row in rows:
                jid = str(row["id"])
                attempts = int(row["attempts"])
                max_attempts = int(row["max_attempts"])
                if attempts >= max_attempts:
                    self._conn.execute(
                        """
                        UPDATE hive_jobs
                           SET status = ?, error = ?, completed_at = ?
                         WHERE id = ?
                        """,
                        (
                            STATUS_FAILED,
                            "node disappeared during dispatch",
                            time.time(), jid,
                        ),
                    )
                    n_failed.append(jid)
                else:
                    self._conn.execute(
                        """
                        UPDATE hive_jobs
                           SET status = ?, node_id = NULL,
                               dispatched_at = NULL,
                               error = ?
                         WHERE id = ?
                        """,
                        (
                            STATUS_QUEUED,
                            "node disappeared, requeued",
                            jid,
                        ),
                    )
                    n_requeued += 1
            self._conn.commit()
        for jid in n_failed:
            self._notify_waiter(jid)
        return n_requeued, len(n_failed)

    def has_active(self) -> bool:
        """Return True if any job is queued or dispatched.

        Idle-path callers (groomer) need a single boolean covering
        both states so a queued-but-not-yet-assigned GPU job blocks
        the groom run as reliably as one already in flight.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM hive_jobs "
                "WHERE status IN ('queued','dispatched') LIMIT 1"
            ).fetchone()
        return row is not None

    def list_recent(
        self,
        *,
        limit: int = 100,
        kind: str | None = None,
        node_id: str | None = None,
        status: str | None = None,
    ) -> list[HiveJob]:
        """Read-only listing for the admin UI."""
        sql = ["SELECT * FROM hive_jobs"]
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if node_id:
            clauses.append("node_id = ?")
            params.append(node_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql.append("WHERE " + " AND ".join(clauses))
        sql.append("ORDER BY created DESC LIMIT ?")
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(" ".join(sql), tuple(params)).fetchall()
        return [self._row_to_job(dict(r)) for r in rows]

    # Waiter plumbing — populated/used by Task 11 (dispatch_helper). Keep
    # it here so `complete` / `report_adapter_error` / `fail` can fire
    # without needing to know about the helper module.
    def _notify_waiter(self, job_id: str) -> None:
        with self._waiter_lock:
            fut = self._waiters.pop(job_id, None)
        if fut is None or fut.done():
            return
        loop = fut.get_loop()
        job = self.get(job_id)
        if job is None:
            return
        # Cross-thread-safe future resolution.
        loop.call_soon_threadsafe(
            lambda: fut.set_result(job) if not fut.done() else None,
        )
