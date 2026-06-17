"""Tests for thread_rename, thread_unarchive, thread_pin RPCs on VaultIndex."""
import time
from pathlib import Path

from vault_writer.index import VaultIndex


def _open(tmp_path: Path) -> VaultIndex:
    return VaultIndex.open(tmp_path / "vault.db", dimension=384)


def _seed_thread(idx: VaultIndex) -> str:
    now = int(time.time())
    idx.thread_create(
        thread_id="t-1", bot="terry", user_id=1,
        title="auto-title", created_at=now,
    )
    return "t-1"


def test_thread_rename_sets_title_and_locks(tmp_path: Path) -> None:
    idx = _open(tmp_path)
    try:
        tid = _seed_thread(idx)
        idx.thread_rename(thread_id=tid, title="My Project")
        row = idx.thread_get(tid)
        assert row["title"] == "My Project"
        assert row["title_locked"] == 1
    finally:
        idx.close()


def test_thread_unarchive_clears_archived_at(tmp_path: Path) -> None:
    idx = _open(tmp_path)
    try:
        tid = _seed_thread(idx)
        idx.thread_archive(thread_id=tid, archived_at=int(time.time()))
        assert idx.thread_get(tid)["archived_at"] is not None
        idx.thread_unarchive(thread_id=tid)
        assert idx.thread_get(tid)["archived_at"] is None
    finally:
        idx.close()


def test_thread_pin_toggles(tmp_path: Path) -> None:
    idx = _open(tmp_path)
    try:
        tid = _seed_thread(idx)
        assert idx.thread_get(tid)["pinned"] == 0
        idx.thread_pin(thread_id=tid, pinned=True)
        assert idx.thread_get(tid)["pinned"] == 1
        idx.thread_pin(thread_id=tid, pinned=False)
        assert idx.thread_get(tid)["pinned"] == 0
    finally:
        idx.close()
