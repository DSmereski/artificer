"""SQLite + sqlite-vec + FTS5 wrapper for the vault index.

Search is hybrid: a vector search and an FTS5 BM25 search run in
parallel and their rankings are combined via Reciprocal Rank Fusion
(RRF). Vector wins on semantic / paraphrase queries; FTS5 wins on
exact-term recall. Together they handle both "what's the kraken?"
(rare proper noun, FTS5 dominates) and "spaceships drake makes"
(paraphrase, vector dominates) without per-query tuning.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlite_vec

from vault_writer.util import AUDIENCE_OVERSCAN_FACTOR


# RRF constant. 60 is the value the original RRF paper used and most
# implementations stick with. Ranks are 1-indexed; score contribution
# from one ranker is 1 / (RRF_K + rank).
_RRF_K = 60

# Boost added to a candidate's RRF score when the query has a
# whole-substring match against the note's filename stem or
# frontmatter title. Larger than any single-ranker contribution
# (max 1/61 ≈ 0.0164) so title hits dominate, but small enough that
# the boost ordering itself still comes from RRF ranks when multiple
# notes match.
_TITLE_BOOST = 0.1


from shared.slug_utils import title_from_slug as _title_from_slug


def _resolve_fts_fields(path: str, frontmatter: dict, body: str) -> tuple[str, str]:
    """Return (title, tags_joined) for the FTS row.

    Title prefers frontmatter, else a slug-derived title from the
    filename stem (matches the same fallback as the gateway's autolink
    and search-title resolution). Tags become space-joined so FTS can
    treat them as bag-of-words.
    """
    title = frontmatter.get("title") if isinstance(frontmatter, dict) else None
    if not (isinstance(title, str) and title.strip()):
        # Strip vault subfolder from path; we want just the file's stem.
        from pathlib import PurePosixPath
        title = _title_from_slug(PurePosixPath(path).stem)
    tags = frontmatter.get("tags") if isinstance(frontmatter, dict) else None
    if isinstance(tags, list):
        tags_joined = " ".join(str(t) for t in tags if isinstance(t, (str, int)))
    elif isinstance(tags, str):
        tags_joined = tags
    else:
        tags_joined = ""
    return title, tags_joined


@dataclass(frozen=True, slots=True)
class SearchResult:
    path: str
    note_type: str
    author: str
    audience: list[str]
    body: str
    frontmatter: dict
    score: float


def _pack_vec(vec: Iterable[float]) -> bytes:
    floats = list(vec)
    return struct.pack(f"{len(floats)}f", *floats)


class VaultIndex:
    """Thin SQLite/sqlite-vec layer. Not thread-safe; daemon uses a single task."""

    def __init__(self, conn: sqlite3.Connection, dimension: int) -> None:
        self._conn = conn
        self._dimension = dimension

    @classmethod
    def open(cls, db_path: Path, dimension: int) -> "VaultIndex":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # 5-second busy timeout so concurrent reads that race with the
        # daemon's commits don't fail outright. The vault_client adds a
        # higher-level retry on top.
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY,
                path        TEXT NOT NULL UNIQUE,
                note_type   TEXT NOT NULL,
                author      TEXT NOT NULL,
                audience    TEXT NOT NULL,
                frontmatter TEXT NOT NULL,
                body        TEXT NOT NULL,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_notes
            USING vec0(embedding FLOAT[{dimension}])
            """
        )
        # Full-text index over title + body + tags, used as half of the
        # hybrid search (vector + BM25, fused via RRF). FTS5 ships with
        # SQLite by default — no extension load needed. `path` is
        # stored UNINDEXED so we can JOIN on it without bloating the
        # token map. Porter stemming so "kraken" matches "krakens".
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
            USING fts5(
                path UNINDEXED,
                title,
                body,
                tags,
                tokenize = 'porter unicode61'
            )
            """
        )
        # ---------------------------------------------------------- chat_log
        # Append-only record of every Hive turn so "what did we say
        # about X" has a real index to query. Lives in the vault DB
        # (not the vault filesystem) — high-churn, noisy, would
        # balloon the git repo. FTS5 only for now; embeddings can
        # ride on later via the same source_kind discriminator the
        # vault embed_chunks table already supports.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL DEFAULT 'default',
                turn_id TEXT,
                bot TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                parent_id INTEGER,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chat_log_user_thread "
            "ON chat_log(bot, user_id, thread_id, created_at)"
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chat_log_fts
            USING fts5(
                content,
                content='chat_log',
                content_rowid='id',
                tokenize='porter unicode61'
            )
            """
        )
        # External-content FTS5: triggers keep the index in sync with
        # chat_log inserts/updates/deletes. Without these the FTS table
        # never sees new rows even though the base table has them.
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chat_log_ai
            AFTER INSERT ON chat_log BEGIN
                INSERT INTO chat_log_fts(rowid, content)
                VALUES (new.id, new.content);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chat_log_ad
            AFTER DELETE ON chat_log BEGIN
                INSERT INTO chat_log_fts(chat_log_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS chat_log_au
            AFTER UPDATE ON chat_log BEGIN
                INSERT INTO chat_log_fts(chat_log_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
                INSERT INTO chat_log_fts(rowid, content)
                VALUES (new.id, new.content);
            END
            """
        )
        # ---------------------------------------------------------- chat_thread
        # Per-conversation metadata. `id` is a ULID generated by the
        # gateway; we don't generate IDs in SQL because the daemon
        # protocol carries them through. parent_thread_id +
        # fork_point_turn_id let us materialise "fork from this turn"
        # as a new thread that copies chat_log rows up to that point.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_thread (
                id TEXT PRIMARY KEY,
                bot TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at INTEGER NOT NULL,
                last_active_at INTEGER NOT NULL,
                archived_at INTEGER,
                parent_thread_id TEXT,
                fork_point_turn_id TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chat_thread_active "
            "ON chat_thread(bot, user_id, last_active_at)"
        )
        # 2026-05-08 (#TBD): user-set names + pinned threads
        cols = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(chat_thread)"
            ).fetchall()
        }
        if "title_locked" not in cols:
            conn.execute(
                "ALTER TABLE chat_thread "
                "ADD COLUMN title_locked INTEGER NOT NULL DEFAULT 0"
            )
        if "pinned" not in cols:
            conn.execute(
                "ALTER TABLE chat_thread "
                "ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chat_thread_pinned "
            "ON chat_thread(bot, user_id, pinned, last_active_at)"
        )
        # ---------------------------------------------------------- entity_page
        # Phase 3: gbrain-openclaw-shaped entity index. `compiled_truth`
        # is a mutable summary (synthesizer rewrites it as new info
        # arrives); `timeline` is append-only — never modified after a
        # row's first write within a turn — so we can flag contradictions
        # later without losing provenance. The FTS5 mirror lets the
        # planner fan out a query like "what do we know about kraken?"
        # alongside notes_fts and chat_log_fts.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_page (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                compiled_truth TEXT,
                timeline TEXT,
                created_at INTEGER NOT NULL,
                last_mentioned_at INTEGER NOT NULL,
                relationships TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        # Phase 3 (#456): relationships column carries graphify-style
        # confidence-tagged edges as JSON. ALTER TABLE adds it
        # idempotently — `IF NOT EXISTS` is not valid for ALTER TABLE
        # so we probe pragma first.
        cols = {
            r[1] for r in conn.execute(
                "PRAGMA table_info(entity_page)"
            ).fetchall()
        }
        if "relationships" not in cols:
            conn.execute(
                "ALTER TABLE entity_page "
                "ADD COLUMN relationships TEXT NOT NULL DEFAULT '[]'"
            )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS entity_page_fts USING fts5(
                title, compiled_truth, content=entity_page,
                content_rowid=rowid, tokenize='unicode61'
            )
            """
        )
        # FTS5 mirror triggers — same shape as chat_log_ai/au/ad. We
        # index by the entity_page rowid (auto-assigned), keyed back
        # via id elsewhere.
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS entity_page_ai
               AFTER INSERT ON entity_page BEGIN
                 INSERT INTO entity_page_fts(rowid, title, compiled_truth)
                 VALUES (new.rowid, new.title,
                         COALESCE(new.compiled_truth, ''));
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS entity_page_au
               AFTER UPDATE ON entity_page BEGIN
                 INSERT INTO entity_page_fts(entity_page_fts, rowid, title,
                                             compiled_truth)
                 VALUES('delete', old.rowid, old.title,
                        COALESCE(old.compiled_truth, ''));
                 INSERT INTO entity_page_fts(rowid, title, compiled_truth)
                 VALUES (new.rowid, new.title,
                         COALESCE(new.compiled_truth, ''));
               END"""
        )
        conn.execute(
            """CREATE TRIGGER IF NOT EXISTS entity_page_ad
               AFTER DELETE ON entity_page BEGIN
                 INSERT INTO entity_page_fts(entity_page_fts, rowid, title,
                                             compiled_truth)
                 VALUES('delete', old.rowid, old.title,
                        COALESCE(old.compiled_truth, ''));
               END"""
        )
        # ---------------------------------------------------------- note_chunks
        # Per-chunk vector table so every segment of a long note is
        # independently searchable. The legacy vec_notes table (one row
        # per note, first-chunk only) is kept unchanged for backward
        # compatibility; note_chunks supplements it.
        #
        # Schema:
        #   note_id    — FK → notes.id  (not enforced in SQLite, but documented)
        #   chunk_idx  — 0-based chunk position within the note
        #   embedding  — F32 vector, same dimension as vec_notes
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS note_chunks (
                id        INTEGER PRIMARY KEY,
                note_id   INTEGER NOT NULL,
                chunk_idx INTEGER NOT NULL,
                UNIQUE(note_id, chunk_idx)
            )
            """
        )
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_note_chunks
            USING vec0(embedding FLOAT[{dimension}])
            """
        )
        conn.commit()
        return cls(conn, dimension)

    def close(self) -> None:
        self._conn.close()

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM notes")
        return int(cur.fetchone()[0])

    def upsert(
        self,
        *,
        path: str,
        note_type: str,
        author: str,
        audience: list[str],
        frontmatter: dict,
        body: str,
        embedding: list[float],
    ) -> None:
        if len(embedding) != self._dimension:
            raise ValueError(f"embedding dim {len(embedding)} != {self._dimension}")
        audience_json = json.dumps(audience)
        fm_json = json.dumps(frontmatter, default=str)
        title, tags_joined = _resolve_fts_fields(path, frontmatter, body)

        cur = self._conn.execute("SELECT id FROM notes WHERE path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            cur = self._conn.execute(
                """INSERT INTO notes (path, note_type, author, audience, frontmatter, body)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (path, note_type, author, audience_json, fm_json, body),
            )
            note_id = cur.lastrowid
            self._conn.execute(
                "INSERT INTO vec_notes (rowid, embedding) VALUES (?, ?)",
                (note_id, _pack_vec(embedding)),
            )
            self._conn.execute(
                "INSERT INTO notes_fts (rowid, path, title, body, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (note_id, path, title, body, tags_joined),
            )
        else:
            note_id = int(row["id"])
            self._conn.execute(
                """UPDATE notes
                   SET note_type = ?, author = ?, audience = ?, frontmatter = ?,
                       body = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (note_type, author, audience_json, fm_json, body, note_id),
            )
            self._conn.execute("DELETE FROM vec_notes WHERE rowid = ?", (note_id,))
            self._conn.execute(
                "INSERT INTO vec_notes (rowid, embedding) VALUES (?, ?)",
                (note_id, _pack_vec(embedding)),
            )
            self._conn.execute(
                "DELETE FROM notes_fts WHERE rowid = ?", (note_id,),
            )
            self._conn.execute(
                "INSERT INTO notes_fts (rowid, path, title, body, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (note_id, path, title, body, tags_joined),
            )
        self._conn.commit()

    def upsert_chunks(self, note_id: int, embeddings: list[list[float]]) -> None:
        """Replace all per-chunk vectors for `note_id` with `embeddings`.

        Deletes any existing chunks for the note first, then inserts the
        new set. The note_id must already exist in the `notes` table —
        callers (daemon._do_upsert, _do_learn) are responsible for
        calling `upsert` before this method.

        Called by the daemon whenever it embeds a note; also used by
        `reindex_chunks` to back-fill existing rows.
        """
        # Remove stale chunk rows and their vectors.
        old_rows = self._conn.execute(
            "SELECT id FROM note_chunks WHERE note_id = ?", (note_id,)
        ).fetchall()
        for r in old_rows:
            self._conn.execute(
                "DELETE FROM vec_note_chunks WHERE rowid = ?", (int(r["id"]),)
            )
        self._conn.execute(
            "DELETE FROM note_chunks WHERE note_id = ?", (note_id,)
        )
        # Insert new chunk rows and their vectors.
        for idx, vec in enumerate(embeddings):
            if len(vec) != self._dimension:
                raise ValueError(
                    f"chunk {idx} dim {len(vec)} != {self._dimension}"
                )
            cur = self._conn.execute(
                "INSERT INTO note_chunks (note_id, chunk_idx) VALUES (?, ?)",
                (note_id, idx),
            )
            chunk_rowid = cur.lastrowid
            self._conn.execute(
                "INSERT INTO vec_note_chunks (rowid, embedding) VALUES (?, ?)",
                (chunk_rowid, _pack_vec(vec)),
            )
        self._conn.commit()

    def search_by_chunks(
        self,
        query_embedding: list[float],
        *,
        k: int,
        audience: str,
        query_text: str | None = None,
    ) -> list["SearchResult"]:
        """Hybrid search that queries per-chunk vectors, then max-pools to notes.

        The per-chunk kNN finds semantically relevant passages anywhere in
        a note (not just the opening chunk). We then deduplicate to one row
        per note by keeping the best (lowest-distance) chunk match, before
        passing note IDs through the same RRF + audience filter used by
        `search`. Falls back to `search` when the note_chunks table is
        empty (e.g. fresh install before the first reindex).
        """
        if len(query_embedding) != self._dimension:
            raise ValueError(
                f"query dim {len(query_embedding)} != {self._dimension}"
            )

        # Check whether any chunks exist; fall back to note-level search if not.
        chunk_count = self._conn.execute(
            "SELECT COUNT(*) FROM note_chunks"
        ).fetchone()[0]
        if chunk_count == 0:
            return self.search(
                query_embedding, k=k, audience=audience, query_text=query_text
            )

        overscan = k * AUDIENCE_OVERSCAN_FACTOR

        # --- Chunk-level kNN -------------------------------------------------
        chunk_rows = self._conn.execute(
            """
            SELECT nc.note_id, vc.distance
            FROM vec_note_chunks vc
            JOIN note_chunks nc ON nc.id = vc.rowid
            WHERE vc.embedding MATCH ? AND k = ?
            ORDER BY vc.distance
            """,
            (_pack_vec(query_embedding), overscan * 4),
        ).fetchall()

        # Max-pool: keep the best (lowest distance) chunk per note.
        best: dict[int, float] = {}
        for row in chunk_rows:
            nid = int(row["note_id"])
            dist = float(row["distance"])
            if nid not in best or dist < best[nid]:
                best[nid] = dist

        if not best:
            return self.search(
                query_embedding, k=k, audience=audience, query_text=query_text
            )

        # Retrieve note metadata for the surviving candidates.
        placeholders = ",".join("?" * len(best))
        note_rows = self._conn.execute(
            f"""
            SELECT id, path, note_type, author, audience, frontmatter, body
            FROM notes WHERE id IN ({placeholders})
            """,
            list(best.keys()),
        ).fetchall()

        # Build a lookup keyed by note_id so we can marry with distances.
        note_map: dict[int, object] = {int(r["id"]): r for r in note_rows}

        # --- FTS5 half (optional) --------------------------------------------
        fts_ranks: dict[int, int] = {}
        fts_query = (
            _coerce_fts_query(query_text, operator="OR")
            if query_text else None
        )
        if fts_query:
            try:
                fts_rows = self._conn.execute(
                    """
                    SELECT n.id, bm25(notes_fts) AS rank
                    FROM notes_fts f
                    JOIN notes n ON n.id = f.rowid
                    WHERE notes_fts MATCH ?
                    ORDER BY bm25(notes_fts)
                    LIMIT ?
                    """,
                    (fts_query, overscan),
                ).fetchall()
                for fts_rank, r in enumerate(fts_rows, start=1):
                    fts_ranks[int(r["id"])] = fts_rank
            except Exception:  # noqa: BLE001
                pass

        # --- RRF fusion on best-chunk distances + FTS ranks ------------------
        # Rank the notes by their best-chunk distance (closest = rank 1).
        sorted_by_dist = sorted(best.items(), key=lambda x: x[1])
        rrf: dict[int, float] = {}
        for vec_rank, (nid, _) in enumerate(sorted_by_dist, start=1):
            rrf[nid] = rrf.get(nid, 0.0) + 1.0 / (_RRF_K + vec_rank)
        for nid, fts_rank in fts_ranks.items():
            rrf[nid] = rrf.get(nid, 0.0) + 1.0 / (_RRF_K + fts_rank)

        # Title-stem boost (mirrors search()).
        if query_text:
            qt_lc = query_text.lower()
            stop = {"the", "and", "for", "with", "from", "what", "are",
                    "is", "has", "have", "was", "were", "this", "that",
                    "you", "your", "tell", "give", "show", "make"}
            tokens = [
                t for t in re.split(r"[^A-Za-z0-9]+", qt_lc)
                if len(t) >= 3 and t not in stop and not t.isdigit()
            ]
            for nid, row in note_map.items():
                if nid not in rrf:
                    continue
                path = row["path"]
                stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                fm = {}
                try:
                    fm = json.loads(row["frontmatter"])
                except (json.JSONDecodeError, TypeError):
                    pass
                title = str(fm.get("title", "")).lower()
                matched = sum(
                    1 for tok in tokens if tok in stem or tok in title
                )
                if matched:
                    rrf[nid] = rrf.get(nid, 0.0) + _TITLE_BOOST * matched

        ordered = sorted(rrf.items(), key=lambda x: x[1], reverse=True)

        results: list[SearchResult] = []
        for nid, score in ordered:
            row = note_map.get(nid)
            if row is None:
                continue
            aud = json.loads(row["audience"])
            if audience != "all" and "all" not in aud and audience not in aud:
                continue
            results.append(
                SearchResult(
                    path=row["path"],
                    note_type=row["note_type"],
                    author=row["author"],
                    audience=aud,
                    body=row["body"],
                    frontmatter=json.loads(row["frontmatter"]),
                    score=round(score, 6),
                )
            )
            if len(results) >= k:
                break
        return results

    def reindex_chunks_count(self) -> int:
        """Return how many notes currently lack per-chunk vectors.

        Used to decide whether `reindex_chunks` needs to run on startup.
        A note 'lacks' chunk vectors when its id does not appear in
        `note_chunks` at all (i.e. was indexed before this feature
        shipped).
        """
        indexed = self._conn.execute(
            "SELECT COUNT(DISTINCT note_id) FROM note_chunks"
        ).fetchone()[0]
        total = self._conn.execute(
            "SELECT COUNT(*) FROM notes"
        ).fetchone()[0]
        return max(0, int(total) - int(indexed))

    def delete(self, path: str) -> None:
        cur = self._conn.execute("SELECT id FROM notes WHERE path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            return
        note_id = int(row["id"])
        # Remove per-chunk vectors first.
        old_chunks = self._conn.execute(
            "SELECT id FROM note_chunks WHERE note_id = ?", (note_id,)
        ).fetchall()
        for r in old_chunks:
            self._conn.execute(
                "DELETE FROM vec_note_chunks WHERE rowid = ?", (int(r["id"]),)
            )
        self._conn.execute(
            "DELETE FROM note_chunks WHERE note_id = ?", (note_id,)
        )
        self._conn.execute("DELETE FROM vec_notes WHERE rowid = ?", (note_id,))
        self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
        self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self._conn.commit()

    def list_note_embeddings(self) -> list[tuple[str, list[float]]]:
        """Yield (path, embedding) for every indexed note.

        Used by the groomer's dup_scanner to do its O(N^2) cosine
        sweep without re-embedding. Cheap-but-not-free: a 1000-note
        vault returns ~1000 * dim * 4 bytes — for the default
        384-dim embedder that's ~1.5 MB held in memory for the
        duration of the scan. Acceptable given groomer runs only
        when idle.

        Returns floats deserialised back from the sqlite-vec packed
        f32 blob so callers can do arithmetic without poking at
        struct.unpack."""
        cur = self._conn.execute(
            "SELECT n.path, v.embedding "
            "FROM notes n JOIN vec_notes v ON v.rowid = n.id"
        )
        out: list[tuple[str, list[float]]] = []
        for row in cur.fetchall():
            blob = bytes(row["embedding"])
            n = len(blob) // 4
            if n == 0:
                continue
            floats = list(struct.unpack(f"{n}f", blob))
            out.append((row["path"], floats))
        return out

    def backfill_fts_if_empty(self) -> int:
        """Populate notes_fts from `notes` if the FTS table is empty.

        Runs once on daemon start so existing vaults whose index
        predates the FTS5 column don't have to be re-indexed by
        re-walking the filesystem. Returns the number of rows
        backfilled. The check + write are wrapped in BEGIN IMMEDIATE
        so two concurrent daemons can't both see count=0 and
        double-insert.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self._conn.execute(
                "SELECT COUNT(*) FROM notes_fts"
            ).fetchone()[0]
            if existing > 0:
                self._conn.execute("ROLLBACK")
                return 0
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        rows = self._conn.execute(
            "SELECT id, path, frontmatter, body FROM notes"
        ).fetchall()
        n = 0
        for r in rows:
            try:
                fm = json.loads(r["frontmatter"])
            except Exception:
                fm = {}
            title, tags_joined = _resolve_fts_fields(r["path"], fm, r["body"])
            self._conn.execute(
                "INSERT INTO notes_fts (rowid, path, title, body, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (int(r["id"]), r["path"], title, r["body"], tags_joined),
            )
            n += 1
        self._conn.commit()
        return n

    def search(
        self,
        query_embedding: list[float],
        *,
        k: int,
        audience: str,
        query_text: str | None = None,
    ) -> list[SearchResult]:
        """Hybrid vector + FTS5 search with Reciprocal Rank Fusion.

        Vector half: cosine-distance kNN over `vec_notes`.
        Keyword half: BM25 over `notes_fts` (title + body + tags).
        Fusion: RRF score = Σ over rankers of 1/(k_const + rank).

        When `query_text` is None or unparseable as an FTS5 query, the
        search degrades cleanly to vector-only.
        """
        if len(query_embedding) != self._dimension:
            raise ValueError(
                f"query dim {len(query_embedding)} != {self._dimension}"
            )

        overscan = k * AUDIENCE_OVERSCAN_FACTOR

        # --- Vector half --------------------------------------------------
        vec_rows = self._conn.execute(
            """
            SELECT n.id, n.path, n.note_type, n.author, n.audience,
                   n.frontmatter, n.body, v.distance
            FROM vec_notes v
            JOIN notes n ON n.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (_pack_vec(query_embedding), overscan),
        ).fetchall()

        # --- FTS5 half ----------------------------------------------------
        # OR-by-default: natural-language LLM queries ("What is the UEE?")
        # would otherwise AND every preposition, demanding every token
        # appear in one note. SC retrieval eval (2026-06-05) showed AND
        # losing the target note entirely. BM25 still ranks notes that
        # match more tokens higher, so the precision cost is small.
        fts_rows: list[sqlite3.Row] = []
        fts_query = (
            _coerce_fts_query(query_text, operator="OR")
            if query_text else None
        )
        if fts_query:
            try:
                fts_rows = self._conn.execute(
                    """
                    SELECT n.id, n.path, n.note_type, n.author, n.audience,
                           n.frontmatter, n.body, bm25(notes_fts) AS rank
                    FROM notes_fts f
                    JOIN notes n ON n.id = f.rowid
                    WHERE notes_fts MATCH ?
                    ORDER BY bm25(notes_fts)
                    LIMIT ?
                    """,
                    (fts_query, overscan),
                ).fetchall()
            except sqlite3.OperationalError:
                # Malformed FTS5 query (e.g. odd punctuation) — skip
                # the keyword half rather than 500.
                fts_rows = []

        # --- RRF fusion --------------------------------------------------
        # Map note_id → (row_data, rrf_score). Vector ranks contribute
        # 1/(K+rank); FTS ranks do too. Notes that appear in both halves
        # accumulate score from each (the whole point of RRF).
        rrf: dict[int, dict] = {}
        for rank, row in enumerate(vec_rows, start=1):
            rrf.setdefault(int(row["id"]), {"row": row, "score": 0.0})
            rrf[int(row["id"])]["score"] += 1.0 / (_RRF_K + rank)
        for rank, row in enumerate(fts_rows, start=1):
            rrf.setdefault(int(row["id"]), {"row": row, "score": 0.0})
            rrf[int(row["id"])]["score"] += 1.0 / (_RRF_K + rank)

        # --- Title-stem boost ---------------------------------------------
        # SC retrieval eval (2026-06-05) showed nomic-embed-text returns
        # garbage for short acronym queries like "UEE" — top-vector hits
        # were unrelated LoRA notes. The FTS leg found the right note,
        # but RRF tie-breaks between rank-1-vector + rank-1-FTS noise.
        # Fix: when query_text contains a strong title-stem match (case-
        # insensitive whole-word in title field or filename stem), boost
        # that note's RRF score by a constant larger than any single-
        # ranker contribution. This makes title hits dominate.
        if query_text:
            qt_lc = query_text.lower()
            # 3+ chars to skip "42", "is", "we"; alphanumeric so we can
            # still match "uee" (3 chars) but not stray digits inside a
            # timestamp filename. Stop-word filter avoids 'the' / 'and'
            # boosting every note in the vault.
            stop = {"the", "and", "for", "with", "from", "what", "are",
                    "is", "has", "have", "was", "were", "this", "that",
                    "you", "your", "tell", "give", "show", "make"}
            tokens = [
                t for t in re.split(r"[^A-Za-z0-9]+", qt_lc)
                if len(t) >= 3 and t not in stop and not t.isdigit()
            ]
            for entry in rrf.values():
                row = entry["row"]
                path = row["path"]
                stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                fm = {}
                try:
                    fm = json.loads(row["frontmatter"])
                except (json.JSONDecodeError, TypeError):
                    pass
                title = str(fm.get("title", "")).lower()
                # Count distinct user-query tokens that match stem or
                # title — multi-token matches outrank single-token, so
                # "ship-manufacturer-drake-interplanetary" beats a note
                # whose only matching token is "drake".
                matched = 0
                for tok in tokens:
                    if tok in stem or tok in title:
                        matched += 1
                if matched:
                    entry["score"] += _TITLE_BOOST * matched

        ordered = sorted(
            rrf.values(), key=lambda e: e["score"], reverse=True,
        )

        results: list[SearchResult] = []
        for entry in ordered:
            row = entry["row"]
            aud = json.loads(row["audience"])
            # Mirrors `vault_writer.util.audience_matches` — when the
            # caller's audience is "all" (the user's privileged
            # devices), they see every note. Without this carve-out,
            # devices paired with audience=["all"] couldn't see notes
            # scoped to bot-only audiences (like "terry"), which is
            # how the app's vault tab silently returned nothing for
            # everything Terry had saved.
            if audience != "all" and "all" not in aud and audience not in aud:
                continue
            results.append(
                SearchResult(
                    path=row["path"],
                    note_type=row["note_type"],
                    author=row["author"],
                    audience=aud,
                    body=row["body"],
                    frontmatter=json.loads(row["frontmatter"]),
                    score=round(entry["score"], 6),
                )
            )
            if len(results) >= k:
                break
        return results

    def get_note(self, path: str) -> SearchResult | None:
        """Read a single indexed note by path. Used by /v1/vault/related
        to fetch the seed note's embedding for nearest-neighbour search."""
        row = self._conn.execute(
            "SELECT id, path, note_type, author, audience, frontmatter, body "
            "FROM notes WHERE path = ?", (path,),
        ).fetchone()
        if row is None:
            return None
        return SearchResult(
            path=row["path"],
            note_type=row["note_type"],
            author=row["author"],
            audience=json.loads(row["audience"]),
            body=row["body"],
            frontmatter=json.loads(row["frontmatter"]),
            score=1.0,
        )

    def neighbours(
        self, path: str, *, k: int, audience: str,
    ) -> list[SearchResult]:
        """Top-k semantically-similar notes to `path`. Excludes the
        seed itself. Audience-filtered."""
        seed = self._conn.execute(
            "SELECT n.id, v.embedding FROM notes n "
            "JOIN vec_notes v ON v.rowid = n.id WHERE n.path = ?",
            (path,),
        ).fetchone()
        if seed is None:
            return []
        seed_id = int(seed["id"])
        # vec0 needs the embedding bytes back.
        rows = self._conn.execute(
            """
            SELECT n.id, n.path, n.note_type, n.author, n.audience,
                   n.frontmatter, n.body, v.distance
            FROM vec_notes v
            JOIN notes n ON n.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (seed["embedding"], (k + 1) * AUDIENCE_OVERSCAN_FACTOR),
        ).fetchall()
        out: list[SearchResult] = []
        for row in rows:
            if int(row["id"]) == seed_id:
                continue
            aud = json.loads(row["audience"])
            if audience != "all" and "all" not in aud and audience not in aud:
                continue
            out.append(
                SearchResult(
                    path=row["path"],
                    note_type=row["note_type"],
                    author=row["author"],
                    audience=aud,
                    body=row["body"],
                    frontmatter=json.loads(row["frontmatter"]),
                    score=1.0 / (1.0 + float(row["distance"])),
                )
            )
            if len(out) >= k:
                break
        return out


    # ============================================================ chat_log
    #
    # The chat_log table is a high-churn append log of Hive turns; not
    # every turn deserves embedding (most are short / banal), so we
    # index by FTS5 only for Phase 1. Phase 3 may add embeddings via
    # `embed_chunks`-style chunks if the FTS hit-rate proves too low.

    def chat_log_append(
        self,
        *,
        thread_id: str,
        bot: str,
        user_id: int,
        role: str,
        content: str,
        turn_id: str | None = None,
        parent_id: int | None = None,
        created_at: int,
    ) -> int:
        """Insert one chat turn row. Returns the new rowid."""
        cur = self._conn.execute(
            """INSERT INTO chat_log
               (thread_id, turn_id, bot, user_id, role, content,
                parent_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, turn_id, bot, int(user_id), role, content,
             parent_id, int(created_at)),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def chat_log_clear(self, *, bot: str, user_id: int) -> int:
        """Delete all chat_log rows for (bot, user_id). Returns the
        number of rows deleted. Called by the daemon's chat_log_clear
        RPC on MemoryStore.reset so chat history is wiped alongside
        the memory sidecar — prevents prior-session info disclosure.

        The FTS5 triggers handle index cleanup automatically on DELETE.
        """
        cur = self._conn.execute(
            "DELETE FROM chat_log WHERE bot = ? AND user_id = ?",
            (bot, int(user_id)),
        )
        self._conn.commit()
        return int(cur.rowcount)

    def search_chat(
        self,
        *,
        bot: str,
        user_id: int,
        query_text: str,
        limit: int = 20,
        thread_id: str | None = None,
    ) -> list[dict]:
        """FTS5 search over chat_log. Returns rows newest-first
        among the BM25 top-`limit`. Audience clamp comes from the
        bot/user_id pair — this is private to the owner. Empty list
        on a malformed FTS query rather than raising.

        Uses OR semantics so casual recall queries like 'what was the
        multiplication answer' rank older rows that contain *some*
        tokens — BM25 surfaces the best match. AND would require every
        token to appear, which fails on short paraphrased turns."""
        fts_query = _coerce_fts_query(query_text, operator="OR")
        if not fts_query:
            return []
        params: list = [fts_query, bot, int(user_id)]
        thread_clause = ""
        if thread_id:
            thread_clause = " AND c.thread_id = ?"
            params.append(thread_id)
        params.append(int(limit))
        try:
            rows = self._conn.execute(
                f"""
                SELECT c.id, c.thread_id, c.turn_id, c.bot, c.user_id,
                       c.role, c.content, c.pinned, c.parent_id,
                       c.created_at, bm25(chat_log_fts) AS rank
                FROM chat_log_fts f
                JOIN chat_log c ON c.id = f.rowid
                WHERE chat_log_fts MATCH ?
                  AND c.bot = ? AND c.user_id = ?
                  {thread_clause}
                ORDER BY bm25(chat_log_fts), c.created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            {
                "id": int(r["id"]),
                "thread_id": r["thread_id"],
                "turn_id": r["turn_id"],
                "bot": r["bot"],
                "user_id": int(r["user_id"]),
                "role": r["role"],
                "content": r["content"],
                "pinned": bool(r["pinned"]),
                "parent_id": (int(r["parent_id"])
                              if r["parent_id"] is not None else None),
                "created_at": int(r["created_at"]),
            }
            for r in rows
        ]

    def chat_log_recent(
        self,
        *,
        bot: str,
        user_id: int,
        limit: int = 50,
        thread_id: str | None = None,
    ) -> list[dict]:
        """Return the most recent `limit` rows from chat_log for
        (bot, user_id), oldest-first (chronological order).

        This is the persistent complement to LLMClient.recent_messages:
        it survives gateway restarts and never loses turns that fell
        off the rolling in-memory buffer.  The caller (chat_messages
        REST endpoint) merges these rows with the rolling buffer so
        the phone sees *all* history, not just the last _MAX_HISTORY
        messages.

        No FTS query — we just want the N most recent rows by
        created_at, then we reverse them to chronological order.
        """
        params: list = [bot, int(user_id)]
        thread_clause = ""
        if thread_id:
            thread_clause = " AND thread_id = ?"
            params.append(thread_id)
        params.append(int(limit))
        try:
            rows = self._conn.execute(
                f"""
                SELECT id, thread_id, turn_id, bot, user_id,
                       role, content, pinned, parent_id, created_at
                FROM chat_log
                WHERE bot = ? AND user_id = ?
                  {thread_clause}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        # Reverse so the result is chronological (oldest first), matching
        # the shape that LLMClient.recent_messages returns.
        return [
            {
                "role": r["role"],
                "content": r["content"],
            }
            for r in reversed(rows)
        ]

    # ---------------------------------------------------------------- threads

    def thread_create(
        self,
        *,
        thread_id: str,
        bot: str,
        user_id: int,
        title: str | None,
        created_at: int,
        parent_thread_id: str | None = None,
        fork_point_turn_id: str | None = None,
    ) -> int:
        """Insert a new thread row. `created_at` doubles as
        last_active_at on insert.

        Idempotent — a duplicate `thread_id` is a no-op (returns 0).
        Returns the number of rows inserted so the WS auto-create path
        can tell whether it was the first turn for a thread."""
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO chat_thread
               (id, bot, user_id, title, created_at, last_active_at,
                parent_thread_id, fork_point_turn_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (thread_id, bot, int(user_id), title,
             int(created_at), int(created_at),
             parent_thread_id, fork_point_turn_id),
        )
        self._conn.commit()
        return cur.rowcount or 0

    def thread_touch(self, *, thread_id: str, last_active_at: int) -> None:
        """Bump last_active_at — called on every turn so the sidebar
        sorts active threads to the top. Cheap; no row-existence check
        because the WS handshake guarantees the thread exists."""
        self._conn.execute(
            "UPDATE chat_thread SET last_active_at = ? WHERE id = ?",
            (int(last_active_at), thread_id),
        )
        self._conn.commit()

    def thread_archive(
        self, *, thread_id: str, archived_at: int,
    ) -> None:
        self._conn.execute(
            "UPDATE chat_thread SET archived_at = ? WHERE id = ?",
            (int(archived_at), thread_id),
        )
        self._conn.commit()

    def thread_unarchive(self, *, thread_id: str) -> None:
        self._conn.execute(
            "UPDATE chat_thread SET archived_at = NULL WHERE id = ?",
            (thread_id,),
        )
        self._conn.commit()

    def thread_set_title(self, *, thread_id: str, title: str) -> None:
        self._conn.execute(
            "UPDATE chat_thread SET title = ? WHERE id = ?",
            (title, thread_id),
        )
        self._conn.commit()

    def thread_rename(self, *, thread_id: str, title: str) -> None:
        title = title.strip()[:200]
        if not title:
            raise ValueError("title required")
        self._conn.execute(
            "UPDATE chat_thread SET title = ?, title_locked = 1 "
            "WHERE id = ?",
            (title, thread_id),
        )
        self._conn.commit()

    def thread_pin(self, *, thread_id: str, pinned: bool) -> None:
        self._conn.execute(
            "UPDATE chat_thread SET pinned = ? WHERE id = ?",
            (1 if pinned else 0, thread_id),
        )
        self._conn.commit()

    def thread_get(self, thread_id: str) -> dict | None:
        row = self._conn.execute(
            """SELECT id, bot, user_id, title, title_locked, created_at,
                      last_active_at, archived_at, parent_thread_id,
                      fork_point_turn_id, pinned
               FROM chat_thread WHERE id = ?""",
            (thread_id,),
        ).fetchone()
        return _row_to_thread(row) if row is not None else None

    def thread_list(
        self,
        *,
        bot: str,
        user_id: int,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Newest-first by last_active_at. Archived threads are hidden
        unless the caller asks for them."""
        clause = "" if include_archived else " AND archived_at IS NULL"
        rows = self._conn.execute(
            f"""SELECT id, bot, user_id, title, title_locked, created_at,
                       last_active_at, archived_at, parent_thread_id,
                       fork_point_turn_id, pinned
                FROM chat_thread
                WHERE bot = ? AND user_id = ?{clause}
                ORDER BY pinned DESC, last_active_at DESC
                LIMIT ?""",
            (bot, int(user_id), int(limit)),
        ).fetchall()
        return [_row_to_thread(r) for r in rows]

    def thread_search(
        self,
        *,
        bot: str,
        user_id: int,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Two-pass thread search: FTS content match + title LIKE, deduped.

        Returns up to `limit` results ordered by relevance (content hits
        first, newest-by-last_active_at within each pass). Each result has
        shape ``{"thread": {...}, "snippet": str}``.
        """
        q = (query or "").strip()
        if not q:
            return []

        # --- Pass 1: content match via FTS5 --------------------------------
        # snippet() cannot appear alongside GROUP BY in SQLite FTS5; we use
        # a correlated subquery to fetch one snippet per matched thread.
        fts_q = _coerce_fts_query(q, operator="AND")
        content_rows: list = []
        if fts_q:
            try:
                content_rows = self._conn.execute(
                    """
                    SELECT t.id, t.bot, t.user_id, t.title, t.created_at,
                           t.last_active_at, t.archived_at, t.parent_thread_id,
                           t.fork_point_turn_id, t.title_locked, t.pinned,
                           (SELECT snippet(chat_log_fts, 0, '[', ']', '...', 12)
                            FROM chat_log_fts
                            JOIN chat_log cl2 ON cl2.id = chat_log_fts.rowid
                            WHERE chat_log_fts MATCH ?
                              AND cl2.thread_id = t.id
                              AND cl2.bot = ? AND cl2.user_id = ?
                            LIMIT 1) AS snip
                    FROM chat_log_fts
                    JOIN chat_log cl ON cl.id = chat_log_fts.rowid
                    JOIN chat_thread t ON t.id = cl.thread_id
                    WHERE chat_log_fts MATCH ?
                      AND t.bot = ? AND t.user_id = ?
                    GROUP BY t.id
                    ORDER BY t.last_active_at DESC
                    LIMIT ?
                    """,
                    (fts_q, bot, int(user_id), fts_q, bot, int(user_id), int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                content_rows = []

        # --- Pass 2: title match via LIKE -----------------------------------
        title_rows = self._conn.execute(
            """
            SELECT id, bot, user_id, title, created_at, last_active_at,
                   archived_at, parent_thread_id, fork_point_turn_id,
                   title_locked, pinned, '' AS snip
            FROM chat_thread
            WHERE bot = ? AND user_id = ?
              AND title LIKE ?
            ORDER BY last_active_at DESC
            LIMIT ?
            """,
            (bot, int(user_id), f"%{q}%", int(limit)),
        ).fetchall()

        # --- Dedup and assemble result list ---------------------------------
        seen: set[str] = set()
        results: list[dict] = []
        for row in list(content_rows) + list(title_rows):
            tid = row["id"]
            if tid in seen:
                continue
            seen.add(tid)
            results.append({
                "thread": {
                    "id": row["id"],
                    "bot": row["bot"],
                    "user_id": int(row["user_id"]),
                    "title": row["title"],
                    "created_at": int(row["created_at"]),
                    "last_active_at": int(row["last_active_at"]),
                    "archived_at": (int(row["archived_at"])
                                    if row["archived_at"] is not None else None),
                    "parent_thread_id": row["parent_thread_id"],
                    "fork_point_turn_id": row["fork_point_turn_id"],
                    "title_locked": bool(row["title_locked"]),
                    "pinned": bool(row["pinned"]),
                },
                "snippet": row["snip"],
            })
            if len(results) >= int(limit):
                break
        return results

    def thread_fork_copy(
        self,
        *,
        new_thread_id: str,
        source_thread_id: str,
        bot: str,
        user_id: int,
        up_to_turn_id: str | None,
    ) -> int:
        """Materialise a fork by copying chat_log rows from the source
        thread up to (and including) the row whose turn_id matches
        `up_to_turn_id`. Returns the count copied. If `up_to_turn_id`
        is None we copy every row in the source thread."""
        if up_to_turn_id is None:
            cutoff_id = None
        else:
            row = self._conn.execute(
                "SELECT id FROM chat_log "
                "WHERE thread_id = ? AND turn_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (source_thread_id, up_to_turn_id),
            ).fetchone()
            cutoff_id = int(row["id"]) if row else None
        params: list = [new_thread_id, source_thread_id, bot, int(user_id)]
        cutoff_clause = ""
        if cutoff_id is not None:
            cutoff_clause = " AND id <= ?"
            params.append(cutoff_id)
        cur = self._conn.execute(
            f"""INSERT INTO chat_log
                (thread_id, turn_id, bot, user_id, role, content,
                 pinned, parent_id, created_at)
                SELECT ?, turn_id, bot, user_id, role, content,
                       pinned, parent_id, created_at
                FROM chat_log
                WHERE thread_id = ? AND bot = ? AND user_id = ?{cutoff_clause}
                ORDER BY id ASC""",
            tuple(params),
        )
        self._conn.commit()
        return cur.rowcount or 0

    def thread_fork(
        self,
        *,
        new_thread_id: str,
        source_thread_id: str,
        bot: str,
        user_id: int,
        title: str | None,
        created_at: int,
        fork_point_turn_id: str | None,
    ) -> dict:
        """Atomic fork: create the new thread row + copy chat_log rows
        in one transaction. Either both succeed or neither persists.

        HIGH-3 (2026-04-29 review): the prior split (thread_create →
        commit, thread_fork_copy → commit) left an orphan thread row
        on the floor whenever the copy step raised. Wrapping both
        inside a single BEGIN IMMEDIATE/COMMIT means a thrown copy
        triggers an automatic ROLLBACK and the thread row is gone too.

        Returns {"created": int, "copied": int}. `created` is 0 if
        the thread_id collided (caller should treat as error — fork
        must never silently merge into an existing thread)."""
        if up_to_turn_id := fork_point_turn_id:
            row = self._conn.execute(
                "SELECT id FROM chat_log "
                "WHERE thread_id = ? AND turn_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (source_thread_id, up_to_turn_id),
            ).fetchone()
            cutoff_id = int(row["id"]) if row else None
        else:
            cutoff_id = None
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cur_thread = self._conn.execute(
                """INSERT OR IGNORE INTO chat_thread
                   (id, bot, user_id, title, created_at, last_active_at,
                    parent_thread_id, fork_point_turn_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_thread_id, bot, int(user_id), title,
                 int(created_at), int(created_at),
                 source_thread_id, fork_point_turn_id),
            )
            created = cur_thread.rowcount or 0
            if created == 0:
                # Roll back; collision is the caller's problem.
                self._conn.execute("ROLLBACK")
                return {"created": 0, "copied": 0}
            params: list = [new_thread_id, source_thread_id, bot, int(user_id)]
            cutoff_clause = ""
            if cutoff_id is not None:
                cutoff_clause = " AND id <= ?"
                params.append(cutoff_id)
            cur_copy = self._conn.execute(
                f"""INSERT INTO chat_log
                    (thread_id, turn_id, bot, user_id, role, content,
                     pinned, parent_id, created_at)
                    SELECT ?, turn_id, bot, user_id, role, content,
                           pinned, parent_id, created_at
                    FROM chat_log
                    WHERE thread_id = ? AND bot = ? AND user_id = ?{cutoff_clause}
                    ORDER BY id ASC""",
                tuple(params),
            )
            copied = cur_copy.rowcount or 0
            self._conn.execute("COMMIT")
            return {"created": created, "copied": copied}
        except Exception:
            try:
                self._conn.execute("ROLLBACK")
            except Exception:
                pass
            raise

    def chat_pin_set(
        self, *, turn_id: str, bot: str, user_id: int, pinned: bool,
    ) -> int:
        """Toggle the pinned flag on every chat_log row carrying this
        (turn_id, bot, user_id). turn_id alone is not unique — collisions
        across bots or users would otherwise let one device's pin flip
        another's row. Returns affected count."""
        cur = self._conn.execute(
            """UPDATE chat_log SET pinned = ?
               WHERE turn_id = ? AND bot = ? AND user_id = ?""",
            (1 if pinned else 0, turn_id, bot, int(user_id)),
        )
        self._conn.commit()
        return cur.rowcount or 0

    # ----------------------------------------------------------- entities

    def entity_page_upsert(
        self, *, slug: str, kind: str, title: str,
        compiled_truth: str, timeline_entry: str,
        now_epoch: int,
        relationships: list[dict] | None = None,
    ) -> dict:
        """Upsert an entity_page row. Returns
        {prior_compiled_truth, prior_existed}. Timeline entries are
        appended (newline-joined) — never overwritten — so a future
        contradiction check can rebuild the history of claims.

        `relationships` is the graphify-shaped edge list:
        `[{"target_slug": str, "label": str,
          "confidence": "EXTRACTED"|"INFERRED"|"AMBIGUOUS"}, ...]`.
        Persisted as a JSON-serialised TEXT column. None or [] writes
        '[]' so the column is never NULL.
        """
        rels_json = json.dumps(list(relationships or []))
        cur = self._conn.execute(
            "SELECT compiled_truth, timeline FROM entity_page WHERE id = ?",
            (slug,),
        )
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                """INSERT INTO entity_page
                     (id, kind, title, compiled_truth, timeline,
                      created_at, last_mentioned_at, relationships)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    slug, kind, title,
                    compiled_truth or "",
                    timeline_entry or "",
                    int(now_epoch), int(now_epoch),
                    rels_json,
                ),
            )
            self._conn.commit()
            return {"prior_compiled_truth": "", "prior_existed": False}
        prior_truth = str(row["compiled_truth"] or "")
        prior_timeline = str(row["timeline"] or "")
        new_timeline = prior_timeline
        if timeline_entry:
            new_timeline = (
                f"{prior_timeline}\n{timeline_entry}".strip()
                if prior_timeline else timeline_entry
            )
        new_truth = compiled_truth if compiled_truth else prior_truth
        # Only overwrite relationships when caller supplied a non-None
        # list. None preserves the existing column; an empty [] is an
        # explicit "this entity has no edges" assertion and DOES
        # overwrite.
        if relationships is None:
            self._conn.execute(
                """UPDATE entity_page
                     SET kind = ?, title = ?, compiled_truth = ?,
                         timeline = ?, last_mentioned_at = ?
                   WHERE id = ?""",
                (kind, title, new_truth, new_timeline,
                 int(now_epoch), slug),
            )
        else:
            self._conn.execute(
                """UPDATE entity_page
                     SET kind = ?, title = ?, compiled_truth = ?,
                         timeline = ?, last_mentioned_at = ?,
                         relationships = ?
                   WHERE id = ?""",
                (kind, title, new_truth, new_timeline,
                 int(now_epoch), rels_json, slug),
            )
        self._conn.commit()
        return {"prior_compiled_truth": prior_truth, "prior_existed": True}

    def entity_page_get(self, slug: str) -> dict | None:
        row = self._conn.execute(
            """SELECT id, kind, title, compiled_truth, timeline,
                      created_at, last_mentioned_at, relationships
               FROM entity_page WHERE id = ?""",
            (slug,),
        ).fetchone()
        if row is None:
            return None
        rels_raw = row["relationships"] if "relationships" in row.keys() else None
        rels: list = []
        if rels_raw:
            try:
                parsed = json.loads(rels_raw)
                if isinstance(parsed, list):
                    rels = parsed
            except (json.JSONDecodeError, TypeError):
                # Hand-edited / corrupt JSON falls back to empty.
                # Logged once so operators see the bad row.
                import logging as _logging
                _logging.getLogger("vault_writer.index").warning(
                    "entity_page %s has malformed relationships JSON; "
                    "returning empty list", slug,
                )
        return {
            "id": row["id"], "kind": row["kind"], "title": row["title"],
            "compiled_truth": row["compiled_truth"] or "",
            "timeline": row["timeline"] or "",
            "created_at": int(row["created_at"]),
            "last_mentioned_at": int(row["last_mentioned_at"]),
            "relationships": rels,
        }

    def list_entity_pages_for_contradiction_scan(self) -> list[dict]:
        """Yield rows the groomer's contradiction_scanner can compare.

        Returns id, title, compiled_truth, and recent_timeline_entry
        (the LAST line of the newline-joined timeline column). The
        scanner embeds these on the fly via `ctx.embedder` and flags
        an entity when compiled_truth and the recent timeline diverge.

        Skips rows where either field is empty — there's nothing to
        compare and the scanner would just compute zero-vector cosine
        and emit a false positive."""
        cur = self._conn.execute(
            "SELECT id, title, compiled_truth, timeline FROM entity_page"
        )
        out: list[dict] = []
        for row in cur.fetchall():
            truth = (row["compiled_truth"] or "").strip()
            timeline = (row["timeline"] or "").strip()
            if not truth or not timeline:
                continue
            # Most-recent entry is the LAST line of the append-only
            # timeline. Empty trailing lines are ignored.
            last_line = ""
            for line in reversed(timeline.splitlines()):
                if line.strip():
                    last_line = line.strip()
                    break
            if not last_line:
                continue
            out.append({
                "id": row["id"],
                "title": row["title"],
                "compiled_truth": truth,
                "recent_timeline_entry": last_line,
            })
        return out

    def count_chat_pinned_since(
        self, *, bot: str, user_id: int, since_epoch: int,
    ) -> int:
        """Count chat_log rows where pinned=1 and created_at >= since.
        Used by the `/v1/digest` endpoint."""
        try:
            row = self._conn.execute(
                """SELECT COUNT(*) AS n FROM chat_log
                   WHERE bot = ? AND user_id = ? AND pinned = 1
                   AND created_at >= ?""",
                (bot, int(user_id), int(since_epoch)),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["n"]) if row else 0

    def search_notes_fts(
        self, query_text: str, *, audience: str, limit: int = 20,
    ) -> list[dict]:
        """FTS5-only notes search (no vector half). Cheap entry point
        for the unified `/v1/search` fan-out — embeds are wasted there
        because we already have many other surfaces to query."""
        fts_query = _coerce_fts_query(query_text) if query_text else None
        if not fts_query:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT n.id, n.path, n.note_type, n.author, n.audience,
                       n.frontmatter, n.body, bm25(notes_fts) AS rank
                FROM notes_fts f
                JOIN notes n ON n.id = f.rowid
                WHERE notes_fts MATCH ?
                ORDER BY bm25(notes_fts)
                LIMIT ?
                """,
                (fts_query, int(limit) * 4),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        out: list[dict] = []
        for r in rows:
            aud = json.loads(r["audience"])
            if audience != "all" and "all" not in aud and audience not in aud:
                continue
            out.append({
                "path": r["path"],
                "note_type": r["note_type"],
                "author": r["author"],
                "audience": aud,
                "body": r["body"],
                "frontmatter": json.loads(r["frontmatter"]),
                "rank": float(r["rank"]),
            })
            if len(out) >= int(limit):
                break
        return out

    def entity_page_search(self, query: str, *, limit: int = 20) -> list[dict]:
        """FTS5 search over title + compiled_truth. Falls back to a LIKE
        scan when the query coerces to nothing (e.g. punctuation-only)."""
        from vault_writer.index import _coerce_fts_query  # self-ref ok
        coerced = _coerce_fts_query(query) if query else None
        if coerced:
            rows = self._conn.execute(
                """SELECT e.id, e.kind, e.title, e.compiled_truth,
                          e.timeline, e.created_at, e.last_mentioned_at
                   FROM entity_page_fts f
                   JOIN entity_page e ON e.rowid = f.rowid
                   WHERE entity_page_fts MATCH ?
                   ORDER BY e.last_mentioned_at DESC
                   LIMIT ?""",
                (coerced, int(limit)),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT id, kind, title, compiled_truth, timeline,
                          created_at, last_mentioned_at
                   FROM entity_page
                   ORDER BY last_mentioned_at DESC
                   LIMIT ?""",
                (int(limit),),
            ).fetchall()
        return [
            {
                "id": r["id"], "kind": r["kind"], "title": r["title"],
                "compiled_truth": r["compiled_truth"] or "",
                "timeline": r["timeline"] or "",
                "created_at": int(r["created_at"]),
                "last_mentioned_at": int(r["last_mentioned_at"]),
            }
            for r in rows
        ]

    def chat_get_by_turn_id(self, turn_id: str) -> list[dict]:
        rows = self._conn.execute(
            """SELECT id, thread_id, turn_id, bot, user_id, role, content,
                      pinned, parent_id, created_at
               FROM chat_log WHERE turn_id = ? ORDER BY id ASC""",
            (turn_id,),
        ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "thread_id": r["thread_id"],
                "turn_id": r["turn_id"],
                "bot": r["bot"],
                "user_id": int(r["user_id"]),
                "role": r["role"],
                "content": r["content"],
                "pinned": bool(r["pinned"]),
                "parent_id": (int(r["parent_id"])
                              if r["parent_id"] is not None else None),
                "created_at": int(r["created_at"]),
            }
            for r in rows
        ]


def _row_to_thread(row) -> dict:
    return {
        "id": row["id"],
        "bot": row["bot"],
        "user_id": int(row["user_id"]),
        "title": row["title"],
        "title_locked": int(row["title_locked"]) if "title_locked" in row.keys() else 0,
        "created_at": int(row["created_at"]),
        "last_active_at": int(row["last_active_at"]),
        "archived_at": (int(row["archived_at"])
                        if row["archived_at"] is not None else None),
        "parent_thread_id": row["parent_thread_id"],
        "fork_point_turn_id": row["fork_point_turn_id"],
        "pinned": int(row["pinned"]) if "pinned" in row.keys() else 0,
    }


# FTS5 reserves a handful of characters; pre-process the query so
# casual searches like 'kraken-star-citizen' or 'C++ notes' don't blow
# up parsing. We keep alphanumerics, dashes between digits, and
# whitespace; everything else becomes a space.
import re as _re_fts


_FTS_KEEP = _re_fts.compile(r"[A-Za-z0-9_]+")


def _coerce_fts_query(text: str, *, operator: str = "AND") -> str | None:
    """Turn a casual user query into a safe FTS5 MATCH expression.

    Splits on non-word characters, drops empties, and joins with the
    requested operator. Default AND is right for precise notes hybrid
    search (paired with vector kNN). OR is right for chat_recall where
    BM25 must rank loose natural-language matches against short turns.
    Returns None for fully-empty input so callers can skip the FTS
    half entirely.
    """
    if not isinstance(text, str):
        return None
    if operator not in ("AND", "OR"):
        raise ValueError(f"operator must be AND or OR, got {operator!r}")
    tokens = _FTS_KEEP.findall(text)
    if not tokens:
        return None
    quoted = [f'"{t}"' for t in tokens if t]
    if not quoted:
        return None
    if operator == "OR":
        return " OR ".join(quoted)
    return " ".join(quoted)
