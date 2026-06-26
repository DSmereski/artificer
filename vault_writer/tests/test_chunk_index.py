"""Tests for per-chunk vector indexing in vault_writer.index.

Covers:
  - upsert_chunks stores chunk vectors and note_chunks rows
  - search_by_chunks can recall a note via a term that only appears in chunk 2+
  - delete cleans up note_chunks / vec_note_chunks rows
  - reindex_chunks_count returns correct pending count
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vault_writer.index import VaultIndex


DIM = 8


def _make_index(tmp_path: Path) -> VaultIndex:
    return VaultIndex.open(tmp_path / "vault.db", dimension=DIM)


# ---------------------------------------------------------------------------
# upsert_chunks
# ---------------------------------------------------------------------------


def test_upsert_chunks_stores_rows(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    try:
        idx.upsert(
            path="k/note.md", note_type="knowledge", author="hive",
            audience=["all"], frontmatter={}, body="text",
            embedding=[0.5] * DIM,
        )
        note_row = idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("k/note.md",)
        ).fetchone()
        note_id = int(note_row["id"])

        vecs = [[0.1] * DIM, [0.9] * DIM]
        idx.upsert_chunks(note_id, vecs)

        chunk_rows = idx._conn.execute(
            "SELECT id, chunk_idx FROM note_chunks WHERE note_id = ? ORDER BY chunk_idx",
            (note_id,),
        ).fetchall()
        assert len(chunk_rows) == 2
        assert [r["chunk_idx"] for r in chunk_rows] == [0, 1]
    finally:
        idx.close()


def test_upsert_chunks_replaces_on_second_call(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    try:
        idx.upsert(
            path="k/note.md", note_type="knowledge", author="hive",
            audience=["all"], frontmatter={}, body="text",
            embedding=[0.5] * DIM,
        )
        note_id = int(idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("k/note.md",)
        ).fetchone()["id"])

        idx.upsert_chunks(note_id, [[0.1] * DIM, [0.9] * DIM])
        # Replace with a single chunk.
        idx.upsert_chunks(note_id, [[0.5] * DIM])

        count = idx._conn.execute(
            "SELECT COUNT(*) FROM note_chunks WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        assert count == 1
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# search_by_chunks: term-in-later-chunk recall
# ---------------------------------------------------------------------------


def test_search_by_chunks_recalls_later_chunk_match(tmp_path: Path) -> None:
    """A query that only matches text in chunk 2 should recall the note."""
    idx = _make_index(tmp_path)
    try:
        # Note 1: has "starship" only in its second chunk.
        # Primary embedding (chunk 0) is far from query.
        # Chunk-1 embedding is very close to query.
        idx.upsert(
            path="k/starship.md", note_type="knowledge", author="hive",
            audience=["all"], frontmatter={"title": "Starship Note"},
            body="Intro text that is generic.",
            embedding=[0.1] * DIM,  # far from query
        )
        note_row = idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("k/starship.md",)
        ).fetchone()
        note_id = int(note_row["id"])
        idx.upsert_chunks(note_id, [
            [0.1] * DIM,   # chunk 0: far
            [0.95] * DIM,  # chunk 1: close to query
        ])

        # Note 2: a completely unrelated note — primary and all chunk vectors far.
        idx.upsert(
            path="k/other.md", note_type="knowledge", author="hive",
            audience=["all"], frontmatter={}, body="Cooking recipes.",
            embedding=[0.9] * DIM,
        )
        other_row = idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("k/other.md",)
        ).fetchone()
        idx.upsert_chunks(int(other_row["id"]), [[0.9] * DIM])

        # Query vector close to chunk-1 of starship note.
        query = [0.95] * DIM
        results = idx.search_by_chunks(query, k=2, audience="all")
        paths = [r.path for r in results]
        assert "k/starship.md" in paths, (
            f"Expected starship.md in results but got {paths}"
        )
    finally:
        idx.close()


def test_search_by_chunks_falls_back_when_empty(tmp_path: Path) -> None:
    """When no chunks exist, search_by_chunks falls back to note-level search."""
    idx = _make_index(tmp_path)
    try:
        idx.upsert(
            path="k/a.md", note_type="knowledge", author="hive",
            audience=["all"], frontmatter={}, body="text",
            embedding=[0.5] * DIM,
        )
        # No chunks inserted — search_by_chunks should fall back to search().
        results = idx.search_by_chunks([0.5] * DIM, k=5, audience="all")
        assert len(results) == 1
        assert results[0].path == "k/a.md"
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# delete removes chunk rows
# ---------------------------------------------------------------------------


def test_delete_removes_chunk_rows(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    try:
        idx.upsert(
            path="k/del.md", note_type="knowledge", author="hive",
            audience=["all"], frontmatter={}, body="text",
            embedding=[0.5] * DIM,
        )
        note_id = int(idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("k/del.md",)
        ).fetchone()["id"])
        idx.upsert_chunks(note_id, [[0.1] * DIM, [0.9] * DIM])

        idx.delete("k/del.md")

        count = idx._conn.execute(
            "SELECT COUNT(*) FROM note_chunks WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        assert count == 0
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# reindex_chunks_count
# ---------------------------------------------------------------------------


def test_reindex_chunks_count_all_pending(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    try:
        for i in range(3):
            idx.upsert(
                path=f"k/note{i}.md", note_type="knowledge", author="hive",
                audience=["all"], frontmatter={}, body="text",
                embedding=[0.5] * DIM,
            )
        # No chunks inserted — all 3 are pending.
        assert idx.reindex_chunks_count() == 3
    finally:
        idx.close()


def test_reindex_chunks_count_decreases_after_upsert(tmp_path: Path) -> None:
    idx = _make_index(tmp_path)
    try:
        for i in range(3):
            idx.upsert(
                path=f"k/note{i}.md", note_type="knowledge", author="hive",
                audience=["all"], frontmatter={}, body="text",
                embedding=[0.5] * DIM,
            )
        # Chunk-index the first note only.
        note_id = int(idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("k/note0.md",)
        ).fetchone()["id"])
        idx.upsert_chunks(note_id, [[0.5] * DIM])

        assert idx.reindex_chunks_count() == 2
    finally:
        idx.close()
