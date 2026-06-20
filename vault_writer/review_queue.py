"""Wiki review queue — surfaces contradictions and gaps for human review.

When wiki_synth's ANALYZE step finds contradictions or knowledge gaps in
the source notes, it queues them here as review items.  The gateway
exposes ``GET /v1/wiki/reviews`` so the dashboard (or companion app) can
poll for open items and surface them in a "needs review" rail.

Schema follows the same ``CREATE TABLE IF NOT EXISTS`` pattern as
``ingest_queue.py`` — safe to call ``ensure_schema`` on every daemon start.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3

log = logging.getLogger("vault_writer.review_queue")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ schema


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the ``wiki_reviews`` table."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wiki_reviews (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            slug         TEXT    NOT NULL,
            kind         TEXT    NOT NULL CHECK (kind IN ('contradiction', 'gap', 'stale')),
            summary      TEXT    NOT NULL,
            source_notes TEXT    NOT NULL DEFAULT '[]',
            status       TEXT    NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'resolved', 'dismissed')),
            created_at   TEXT    NOT NULL,
            resolved_at  TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_wiki_reviews_status
        ON wiki_reviews (status)
    """)
    conn.commit()


# ------------------------------------------------------------------ write


def add_review(
    conn: sqlite3.Connection,
    *,
    slug: str,
    kind: str,
    summary: str,
    source_notes: list[str] | None = None,
) -> int:
    """Insert a review item. Returns the new row id."""
    cur = conn.execute(
        """
        INSERT INTO wiki_reviews (slug, kind, summary, source_notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            slug,
            kind,
            summary,
            json.dumps(source_notes or []),
            _now_iso(),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    log.info("review_queue: added %s for %r (id=%d)", kind, slug, row_id or -1)
    return row_id or -1


# ------------------------------------------------------------------ read


def get_open_reviews(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict]:
    """Return open review items, newest first."""
    rows = conn.execute(
        """
        SELECT id, slug, kind, summary, source_notes, status, created_at
        FROM wiki_reviews
        WHERE status = 'open'
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "slug": r[1],
            "kind": r[2],
            "summary": r[3],
            "source_notes": json.loads(r[4]) if r[4] else [],
            "status": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def count_open(conn: sqlite3.Connection) -> int:
    """Return count of open review items (cheap badge query)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM wiki_reviews WHERE status = 'open'"
    ).fetchone()
    return row[0] if row else 0


# ------------------------------------------------------------------ resolve


def resolve_review(
    conn: sqlite3.Connection,
    review_id: int,
    *,
    status: str = "resolved",
) -> bool:
    """Mark a review item as resolved or dismissed. Returns True if found."""
    if status not in ("resolved", "dismissed"):
        raise ValueError(f"invalid status {status!r}")
    cur = conn.execute(
        """
        UPDATE wiki_reviews
        SET status = ?, resolved_at = ?
        WHERE id = ? AND status = 'open'
        """,
        (status, _now_iso(), review_id),
    )
    conn.commit()
    return (cur.rowcount or 0) > 0
