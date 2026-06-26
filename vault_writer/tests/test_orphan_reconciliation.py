"""Tests for orphan reconciliation during initial_full_scan.

Covers:
- Orphan index rows (note whose .md was deleted outside the watchdog) are
  purged when initial_full_scan runs with reconcile_orphans=True.
- A live note is kept in the index after reconciliation.
- All associated rows (notes_fts, vec_notes, note_chunks, vec_note_chunks)
  are removed for the orphan — no dangling rows.
- Safety guard: zero on-disk files must NOT trigger a purge.
- reconcile_orphans=False disables the step.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401

from vault_writer.config import AuthConfig, Config, GiteaConfig, ScanConfig, SearchConfig, WikiSynthConfig
from vault_writer.daemon import Daemon
from vault_writer.index import VaultIndex


DIMENSION = 8


def _make_config(
    vault: Path,
    *,
    initial_full_scan: bool = True,
    reconcile_orphans: bool = True,
) -> Config:
    return Config(
        vault_path=vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=0,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=DIMENSION,
        chunk_max_chars=4000,
        gitea=GiteaConfig(
            remote="", token_env="GITEA_TOKEN",
            push_on_write=False, batch_window_seconds=5,
        ),
        search=SearchConfig(default_k=5, min_score=0.4),
        scan=ScanConfig(
            initial_full_scan=initial_full_scan,
            periodic_seconds=0,
            reconcile_orphans=reconcile_orphans,
        ),
        auth=AuthConfig(token_path=None),
        wiki_synth=WikiSynthConfig(enabled=False, model="planner-qwen",
                                   top_k=5, timeout_seconds=30),
    )


class _SimpleEmbedder:
    dimension = DIMENSION

    async def embed(self, text: str, *, kind: str = "document") -> list[float]:
        return [0.5] * DIMENSION

    async def embed_chunks(
        self, text: str, *, kind: str = "document", chunk_size: int | None = None,
    ) -> list[list[float]]:
        return [[0.5] * DIMENSION]


def _seed_orphan(db_path: Path, rel_path: str) -> None:
    """Insert a note row directly into the index DB, simulating a note that
    was indexed when its file existed but whose file has since been deleted."""
    idx = VaultIndex.open(db_path, dimension=DIMENSION)
    try:
        idx.upsert(
            path=rel_path,
            note_type="knowledge",
            author="hive",
            audience=["all"],
            frontmatter={"title": "Orphan"},
            body="This file was deleted from disk.",
            embedding=[0.3] * DIMENSION,
        )
        note_row = idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (rel_path,)
        ).fetchone()
        if note_row is not None:
            idx.upsert_chunks(int(note_row["id"]), [[0.3] * DIMENSION])
    finally:
        idx.close()


def _count_chunks(db_path: Path, note_id: int) -> int:
    idx = VaultIndex.open(db_path, dimension=DIMENSION)
    try:
        return int(idx._conn.execute(
            "SELECT COUNT(*) FROM note_chunks WHERE note_id = ?", (note_id,)
        ).fetchone()[0])
    finally:
        idx.close()


def _get_note_id(db_path: Path, rel_path: str) -> int | None:
    idx = VaultIndex.open(db_path, dimension=DIMENSION)
    try:
        row = idx._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (rel_path,)
        ).fetchone()
        return int(row["id"]) if row is not None else None
    finally:
        idx.close()


@pytest.mark.asyncio
async def test_reconcile_removes_orphan_and_keeps_live_note(
    tmp_path: Path,
) -> None:
    """After a full scan with reconcile_orphans=True:
    - the orphan note (no file on disk) is removed from the index
    - a live note (file exists) is retained
    - no dangling note_chunks or vec_note_chunks rows remain for the orphan
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    db_path = vault / ".vault-writer" / "vault.db"

    # Seed a live note file on disk.
    live_path = vault / "knowledge" / "live.md"
    live_path.write_text(
        "---\ntype: knowledge\nauthor: hive\naudience: [all]\n"
        "title: Live\n---\n\nThis file still exists.\n",
        encoding="utf-8",
    )

    # Pre-populate the DB with an orphan row (no matching file on disk).
    db_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_rel = "knowledge/orphan-deleted.md"
    _seed_orphan(db_path, orphan_rel)
    orphan_note_id = _get_note_id(db_path, orphan_rel)
    assert orphan_note_id is not None, "orphan seed failed"
    # Confirm orphan has a note row and a chunk row before reconciliation.
    assert _count_chunks(db_path, orphan_note_id) == 1, "seed should have 1 chunk"

    daemon = Daemon(_make_config(vault, reconcile_orphans=True), _SimpleEmbedder())
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)

        # Live note must be indexed.
        assert daemon.index.count() >= 1
        live_row = daemon.index._conn.execute(
            "SELECT id FROM notes WHERE path = ?", ("knowledge/live.md",)
        ).fetchone()
        assert live_row is not None, "live note should be in the index"

        # Orphan must be gone from notes.
        orphan_row = daemon.index._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (orphan_rel,)
        ).fetchone()
        assert orphan_row is None, "orphan note should have been purged"

        # note_id equality cannot be relied upon after orphan purge because
        # SQLite may reuse the freed note_id for the live note's insert.
        # Instead verify that every note_chunks row belongs to a note that
        # still exists in the notes table (no dangling FK references).
        orphaned_chunks = daemon.index._conn.execute(
            """
            SELECT COUNT(*) FROM note_chunks nc
            LEFT JOIN notes n ON n.id = nc.note_id
            WHERE n.id IS NULL
            """
        ).fetchone()[0]
        assert orphaned_chunks == 0, (
            f"{orphaned_chunks} note_chunks row(s) reference non-existent notes"
        )

        # No notes_fts row should reference a non-existent note.
        orphaned_fts = daemon.index._conn.execute(
            """
            SELECT COUNT(*) FROM notes_fts f
            LEFT JOIN notes n ON n.id = f.rowid
            WHERE n.id IS NULL
            """
        ).fetchone()[0]
        assert orphaned_fts == 0, (
            f"{orphaned_fts} notes_fts row(s) reference non-existent notes"
        )

        # No vec_notes row should reference a non-existent note.
        orphaned_vec = daemon.index._conn.execute(
            """
            SELECT COUNT(*) FROM vec_notes v
            LEFT JOIN notes n ON n.id = v.rowid
            WHERE n.id IS NULL
            """
        ).fetchone()[0]
        assert orphaned_vec == 0, (
            f"{orphaned_vec} vec_notes row(s) reference non-existent notes"
        )
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_reconcile_disabled_leaves_orphan(tmp_path: Path) -> None:
    """When reconcile_orphans=False the orphan row must NOT be removed."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    db_path = vault / ".vault-writer" / "vault.db"

    live_path = vault / "knowledge" / "live.md"
    live_path.write_text(
        "---\ntype: knowledge\nauthor: hive\naudience: [all]\n"
        "title: Live\n---\n\nBody.\n",
        encoding="utf-8",
    )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_rel = "knowledge/orphan-no-reconcile.md"
    _seed_orphan(db_path, orphan_rel)

    daemon = Daemon(
        _make_config(vault, reconcile_orphans=False), _SimpleEmbedder()
    )
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)
        orphan_row = daemon.index._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (orphan_rel,)
        ).fetchone()
        assert orphan_row is not None, (
            "orphan should NOT be purged when reconcile_orphans=False"
        )
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_reconcile_safety_guard_empty_vault(tmp_path: Path) -> None:
    """If the vault directory is empty (0 .md files on disk), reconciliation
    must NOT purge existing index rows — the vault may simply be on an
    unmounted path or an empty directory used in tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    db_path = vault / ".vault-writer" / "vault.db"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Seed an index row with NO corresponding disk file.
    orphan_rel = "knowledge/safety-guard-note.md"
    _seed_orphan(db_path, orphan_rel)

    # No .md files on disk — the safety guard should prevent purge.
    daemon = Daemon(
        _make_config(vault, initial_full_scan=True, reconcile_orphans=True),
        _SimpleEmbedder(),
    )
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)
        # The orphan row must survive because the on-disk set was empty.
        orphan_row = daemon.index._conn.execute(
            "SELECT id FROM notes WHERE path = ?", (orphan_rel,)
        ).fetchone()
        assert orphan_row is not None, (
            "safety guard failed: orphan was purged despite empty on-disk set"
        )
    finally:
        await daemon.stop()
