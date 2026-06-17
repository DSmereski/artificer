"""Tests for chat_thread schema migrations: title_locked + pinned columns."""

from pathlib import Path
import sqlite3

from vault_writer.index import VaultIndex


def test_chat_thread_has_title_locked_and_pinned(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    idx = VaultIndex.open(db, dimension=384)
    try:
        with sqlite3.connect(db) as conn:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(chat_thread)"
            ).fetchall()}
        assert "title_locked" in cols
        assert "pinned" in cols
        with sqlite3.connect(db) as conn:
            idxs = {r[1] for r in conn.execute(
                "PRAGMA index_list(chat_thread)"
            ).fetchall()}
        assert "chat_thread_pinned" in idxs
    finally:
        idx.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "vault.db"
    VaultIndex.open(db, dimension=384).close()
    VaultIndex.open(db, dimension=384).close()  # second open must not raise
