"""Durable ingest queue for vault-writer.

Replaces the fire-and-forget write path with a crash-safe SQLite-backed
queue. Notes are enqueued as JSON payloads (LearnRequest dict), held in
the ``ingest_queue`` table, and drained by the daemon worker.

Design
------
* **Enqueue**: single INSERT; the note is durable the moment it commits.
* **Drain**: pull a batch of pending rows, mark each ``processing``,
  run embed+index, mark ``done`` (or ``failed`` + increment attempts).
* **Crash recovery**: on daemon startup, call ``recover_stuck()`` to
  reset any ``processing`` rows back to ``pending`` (they were interrupted
  mid-flight by a crash or hard kill).
* **Poison-item cap**: once a row reaches ``MAX_ATTEMPTS`` failures it
  moves to ``failed`` permanently and the drain loop skips it, so one bad
  note never blocks the queue.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from vault_writer.index import VaultIndex

log = logging.getLogger("vault_writer.ingest_queue")

# A row that has failed this many times is permanently parked as 'failed'.
MAX_ATTEMPTS = 5

# States
_PENDING = "pending"
_PROCESSING = "processing"
_DONE = "done"
_FAILED = "failed"

# How many pending rows to pull in one drain batch. Keeps latency
# predictable; large vaults won't stall on a single giant batch.
_BATCH_SIZE = 20


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ schema


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the ingest_queue table.

    Follows the same ``CREATE TABLE IF NOT EXISTS`` pattern used by the
    rest of VaultIndex.open() so it can be called safely on every open,
    even against a DB that already has the table from a previous run.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            payload     TEXT    NOT NULL,
            state       TEXT    NOT NULL DEFAULT 'pending',
            attempts    INTEGER NOT NULL DEFAULT 0,
            error       TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Index for the drain query — oldest pending rows first.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ingest_queue_state_id "
        "ON ingest_queue(state, id)"
    )
    conn.commit()


# ------------------------------------------------------------------ enqueue


def enqueue(conn: sqlite3.Connection, payload: dict) -> int:
    """Insert a note payload as a pending queue row.

    ``payload`` is the dict representation of a LearnRequest (all fields
    that ``Daemon._do_learn`` accepts: category, title, body, author,
    audience, tags, extra).  Callers should pass the same dict they would
    have sent directly to ``_do_learn``.

    Returns the new row id.
    """
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO ingest_queue (payload, state, attempts, created_at, updated_at)
           VALUES (?, 'pending', 0, ?, ?)""",
        (json.dumps(payload), now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


# ------------------------------------------------------------------ recovery


def recover_stuck(conn: sqlite3.Connection) -> int:
    """Reset 'processing' rows to 'pending' on daemon startup.

    Any row left in 'processing' was interrupted by a crash or SIGKILL
    (the daemon died between marking it processing and finishing the
    embed+index step). We reset those to 'pending' so they are retried
    on the next drain, up to MAX_ATTEMPTS total.

    Returns the count of rows reset.
    """
    now = _now_iso()
    cur = conn.execute(
        "UPDATE ingest_queue SET state = 'pending', updated_at = ? "
        "WHERE state = 'processing'",
        (now,),
    )
    conn.commit()
    n = cur.rowcount or 0
    if n > 0:
        log.info("ingest_queue: recovered %d stuck 'processing' row(s)", n)
    return n


# ------------------------------------------------------------------ drain


DrainFn = Callable[[dict], Awaitable[None]]
"""Signature of the async function the drain loop calls per payload.

The caller (daemon) passes ``_drain_one`` which runs embed+index for a
single note payload dict.
"""


async def drain(
    conn: sqlite3.Connection,
    process_fn: DrainFn,
    *,
    batch_size: int = _BATCH_SIZE,
    max_attempts: int = MAX_ATTEMPTS,
) -> int:
    """Process up to ``batch_size`` pending rows.

    For each row:
    1. Mark it ``processing`` (visible to crash recovery).
    2. Call ``process_fn(payload_dict)`` — the embed+index step.
    3. On success: mark ``done``.
    4. On exception: increment ``attempts``; if >= ``max_attempts`` mark
       ``failed``; otherwise reset to ``pending`` so it will be retried.

    One row's failure does NOT abort the rest of the batch — the loop
    continues with the next row.

    Returns the count of rows successfully marked ``done`` in this call.
    """
    rows = conn.execute(
        """SELECT id, payload, attempts FROM ingest_queue
           WHERE state = 'pending'
           ORDER BY id ASC
           LIMIT ?""",
        (batch_size,),
    ).fetchall()

    done_count = 0
    for row in rows:
        row_id = int(row["id"])
        attempts = int(row["attempts"])

        # Mark processing before any async work so a crash leaves a
        # 'processing' breadcrumb for recover_stuck().
        now = _now_iso()
        conn.execute(
            "UPDATE ingest_queue SET state = 'processing', updated_at = ? "
            "WHERE id = ?",
            (now, row_id),
        )
        conn.commit()

        try:
            payload = json.loads(row["payload"])
            await process_fn(payload)
        except Exception as exc:  # noqa: BLE001
            new_attempts = attempts + 1
            now_err = _now_iso()
            err_msg = str(exc)[:2000]  # don't let tracebacks blow the column
            if new_attempts >= max_attempts:
                conn.execute(
                    """UPDATE ingest_queue
                          SET state = 'failed', attempts = ?,
                              error = ?, updated_at = ?
                        WHERE id = ?""",
                    (new_attempts, err_msg, now_err, row_id),
                )
                log.error(
                    "ingest_queue: row %d permanently failed after %d attempts: %s",
                    row_id, new_attempts, err_msg,
                )
            else:
                conn.execute(
                    """UPDATE ingest_queue
                          SET state = 'pending', attempts = ?,
                              error = ?, updated_at = ?
                        WHERE id = ?""",
                    (new_attempts, err_msg, now_err, row_id),
                )
                log.warning(
                    "ingest_queue: row %d attempt %d/%d failed: %s",
                    row_id, new_attempts, max_attempts, err_msg,
                )
            conn.commit()
        else:
            now_done = _now_iso()
            conn.execute(
                "UPDATE ingest_queue SET state = 'done', updated_at = ? "
                "WHERE id = ?",
                (now_done, row_id),
            )
            conn.commit()
            done_count += 1

    return done_count
