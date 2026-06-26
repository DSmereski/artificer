"""Tests for VaultIndex.thread_search — FTS content + title LIKE."""
import time
from pathlib import Path

from vault_writer.index import VaultIndex


def _seed(idx: VaultIndex, tid: str, title: str, content: str) -> None:
    now = int(time.time())
    idx.thread_create(
        thread_id=tid, bot="hive", user_id=1,
        title=title, created_at=now,
    )
    idx.chat_log_append(
        thread_id=tid, turn_id=f"tk-{tid}", bot="hive",
        user_id=1, role="user", content=content,
        created_at=now,
    )


def test_thread_search_finds_by_content(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=384)
    try:
        _seed(idx, "t-1", "Random", "we discussed the kraken at length")
        _seed(idx, "t-2", "Other", "totally unrelated weather chat")
        hits = idx.thread_search(
            bot="hive", user_id=1, query="kraken", limit=10,
        )
        ids = [h["thread"]["id"] for h in hits]
        assert "t-1" in ids
        assert "t-2" not in ids
        assert "kraken" in hits[0]["snippet"].lower()
    finally:
        idx.close()


def test_thread_search_finds_by_title(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=384)
    try:
        _seed(idx, "t-3", "Kraken project", "boring content")
        hits = idx.thread_search(
            bot="hive", user_id=1, query="kraken", limit=10,
        )
        ids = [h["thread"]["id"] for h in hits]
        assert "t-3" in ids
    finally:
        idx.close()


def test_thread_search_deduplicates(tmp_path: Path) -> None:
    """Thread matching both content and title should appear only once."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=384)
    try:
        _seed(idx, "t-4", "Kraken project", "we discussed the kraken deeply")
        hits = idx.thread_search(
            bot="hive", user_id=1, query="kraken", limit=10,
        )
        ids = [h["thread"]["id"] for h in hits]
        assert ids.count("t-4") == 1
    finally:
        idx.close()


def test_thread_search_empty_query_returns_empty(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=384)
    try:
        _seed(idx, "t-5", "Some thread", "some content here")
        hits = idx.thread_search(
            bot="hive", user_id=1, query="", limit=10,
        )
        assert hits == []
    finally:
        idx.close()


def test_thread_search_respects_bot_and_user_scope(tmp_path: Path) -> None:
    """Threads for a different bot or user_id must not leak into results."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=384)
    try:
        now = int(time.time())
        # t-6: correct bot+user
        _seed(idx, "t-6", "Random", "kraken sighting confirmed")
        # t-7: different bot
        idx.thread_create(
            thread_id="t-7", bot="other-bot", user_id=1,
            title="Random", created_at=now,
        )
        idx.chat_log_append(
            thread_id="t-7", turn_id="tk-t-7", bot="other-bot",
            user_id=1, role="user", content="kraken sighting",
            created_at=now,
        )
        # t-8: different user_id
        idx.thread_create(
            thread_id="t-8", bot="hive", user_id=999,
            title="Random", created_at=now,
        )
        idx.chat_log_append(
            thread_id="t-8", turn_id="tk-t-8", bot="hive",
            user_id=999, role="user", content="kraken sighting",
            created_at=now,
        )

        hits = idx.thread_search(
            bot="hive", user_id=1, query="kraken", limit=10,
        )
        ids = [h["thread"]["id"] for h in hits]
        assert "t-6" in ids
        assert "t-7" not in ids
        assert "t-8" not in ids
    finally:
        idx.close()


def test_thread_search_result_shape(tmp_path: Path) -> None:
    """Each hit must include the full thread sub-dict and a snippet string."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=384)
    try:
        _seed(idx, "t-9", "Random", "the kraken awakens")
        hits = idx.thread_search(
            bot="hive", user_id=1, query="kraken", limit=10,
        )
        assert len(hits) == 1
        h = hits[0]
        assert "thread" in h
        assert "snippet" in h
        t = h["thread"]
        for key in (
            "id", "bot", "user_id", "title", "created_at",
            "last_active_at", "archived_at", "parent_thread_id",
            "fork_point_turn_id", "title_locked", "pinned",
        ):
            assert key in t, f"missing key: {key}"
        assert isinstance(h["snippet"], str)
        assert isinstance(t["title_locked"], bool)
        assert isinstance(t["pinned"], bool)
    finally:
        idx.close()
