"""Tests for vault_writer.index."""

from __future__ import annotations

from pathlib import Path

from vault_writer.index import VaultIndex, _coerce_fts_query


def test_coerce_fts_query_drops_punctuation() -> None:
    # Casual user input shouldn't blow up FTS5 parsing.
    assert _coerce_fts_query("kraken") == '"kraken"'
    assert _coerce_fts_query("kraken-star-citizen") == '"kraken" "star" "citizen"'
    assert _coerce_fts_query("C++ notes!") == '"C" "notes"'
    assert _coerce_fts_query("") is None
    assert _coerce_fts_query("   ") is None


def test_coerce_fts_query_or_operator() -> None:
    # OR mode for chat_recall — BM25 must rank loose natural-language matches.
    assert _coerce_fts_query("kraken star citizen", operator="OR") == '"kraken" OR "star" OR "citizen"'
    assert _coerce_fts_query("multiplication answer", operator="OR") == '"multiplication" OR "answer"'
    assert _coerce_fts_query("kraken", operator="OR") == '"kraken"'
    assert _coerce_fts_query("", operator="OR") is None


def test_hybrid_search_keyword_finds_notes_vector_misses(tmp_path: Path) -> None:
    """FTS5 half catches a literal-token match the vector half ranks low."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        # Far-apart embeddings; the kraken note is "far" from the
        # query vector but its body contains the literal word.
        idx.upsert(
            path="knowledge/kraken.md", note_type="knowledge",
            author="terry", audience=["all"],
            frontmatter={"title": "Kraken"},
            body="The Kraken is a Drake capital ship in Star Citizen.",
            embedding=[0.99] * 8,
        )
        idx.upsert(
            path="knowledge/unrelated.md", note_type="knowledge",
            author="terry", audience=["all"],
            frontmatter={"title": "Unrelated"},
            body="A note about cooking pasta with herbs.",
            embedding=[0.01] * 8,
        )
        # Vector query close to "unrelated" — without FTS5, kraken
        # would lose. Hybrid must surface it via the keyword half.
        results = idx.search(
            [0.01] * 8, k=2, audience="all", query_text="kraken",
        )
        paths = [r.path for r in results]
        assert "knowledge/kraken.md" in paths
    finally:
        idx.close()


def test_neighbours_excludes_seed(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.upsert(
            path="knowledge/a.md", note_type="knowledge",
            author="terry", audience=["all"],
            frontmatter={"title": "A"}, body="alpha",
            embedding=[0.1] * 8,
        )
        idx.upsert(
            path="knowledge/b.md", note_type="knowledge",
            author="terry", audience=["all"],
            frontmatter={"title": "B"}, body="beta",
            embedding=[0.11] * 8,
        )
        out = idx.neighbours("knowledge/a.md", k=3, audience="all")
        assert all(r.path != "knowledge/a.md" for r in out)
        assert "knowledge/b.md" in [r.path for r in out]
    finally:
        idx.close()


def _vec(n: int, fill: float) -> list[float]:
    return [fill] * n


def test_open_creates_schema(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        assert (tmp_path / "vault.db").exists()
        assert idx.count() == 0
    finally:
        idx.close()


def test_upsert_then_search_returns_note(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.upsert(
            path="canon/maggy.md",
            note_type="canon",
            author="human",
            audience=["all"],
            frontmatter={"title": "Maggy"},
            body="Maggy is the text chat bot.",
            embedding=_vec(8, 0.1),
        )
        idx.upsert(
            path="canon/terry.md",
            note_type="canon",
            author="human",
            audience=["all"],
            frontmatter={"title": "Terry"},
            body="Terry handles voice and images.",
            embedding=_vec(8, 0.9),
        )
        assert idx.count() == 2

        results = idx.search(_vec(8, 0.1), k=2, audience="claude-code")
        assert len(results) == 2
        # closest first
        assert results[0].path == "canon/maggy.md"
        assert results[0].score > results[1].score
        assert results[0].note_type == "canon"
        assert results[0].audience == ["all"]
    finally:
        idx.close()


def test_upsert_is_idempotent_on_path(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.upsert(
            path="canon/x.md", note_type="canon", author="human",
            audience=["all"], frontmatter={}, body="v1",
            embedding=_vec(8, 0.1),
        )
        idx.upsert(
            path="canon/x.md", note_type="canon", author="human",
            audience=["all"], frontmatter={}, body="v2",
            embedding=_vec(8, 0.1),
        )
        assert idx.count() == 1
        results = idx.search(_vec(8, 0.1), k=5, audience="all")
        assert results[0].body == "v2"
    finally:
        idx.close()


def test_search_filters_by_audience(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.upsert(path="a.md", note_type="ops", author="claude-code",
                   audience=["claude-code"], frontmatter={}, body="prefs",
                   embedding=_vec(8, 0.5))
        idx.upsert(path="b.md", note_type="canon", author="human",
                   audience=["all"], frontmatter={}, body="world",
                   embedding=_vec(8, 0.5))

        maggy_results = idx.search(_vec(8, 0.5), k=5, audience="maggy")
        assert [r.path for r in maggy_results] == ["b.md"]

        cc_results = idx.search(_vec(8, 0.5), k=5, audience="claude-code")
        paths = {r.path for r in cc_results}
        assert paths == {"a.md", "b.md"}
    finally:
        idx.close()


def test_delete_removes_note(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.upsert(path="a.md", note_type="canon", author="human",
                   audience=["all"], frontmatter={}, body="x",
                   embedding=_vec(8, 0.5))
        assert idx.count() == 1
        idx.delete("a.md")
        assert idx.count() == 0
    finally:
        idx.close()


# ---------------------------------------------------------------- threads


def test_thread_create_is_idempotent(tmp_path: Path) -> None:
    """Second create with the same id is a no-op (returns 0 rows).

    Required for the WS auto-create path: every turn calls thread_create
    so the row materialises on the first turn for a fresh thread_id,
    but later turns must NOT clobber the existing title."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        rows = idx.thread_create(
            thread_id="t1", bot="terry", user_id=42,
            title="first message of thread", created_at=100,
        )
        assert rows == 1
        rows2 = idx.thread_create(
            thread_id="t1", bot="terry", user_id=42,
            title="DIFFERENT TITLE — should be ignored",
            created_at=200,
        )
        assert rows2 == 0
        # Title from the original create wins.
        thread = idx.thread_get(thread_id="t1")
        assert thread is not None
        assert thread["title"] == "first message of thread"
        assert thread["created_at"] == 100
    finally:
        idx.close()


def test_thread_touch_no_op_for_missing_row(tmp_path: Path) -> None:
    """thread_touch on a nonexistent id must not raise — the WS path
    fires it speculatively before thread_create has finished."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.thread_touch(thread_id="nonexistent", last_active_at=999)
        # No row created, no error.
        assert idx.thread_get(thread_id="nonexistent") is None
    finally:
        idx.close()


def test_thread_touch_bumps_last_active_at(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.thread_create(thread_id="t1", bot="terry", user_id=1,
                          title="x", created_at=100)
        idx.thread_touch(thread_id="t1", last_active_at=500)
        thread = idx.thread_get(thread_id="t1")
        assert thread["last_active_at"] == 500
        assert thread["created_at"] == 100  # unchanged
    finally:
        idx.close()


def test_thread_list_orders_by_last_active_desc(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.thread_create(thread_id="old", bot="terry", user_id=1,
                          title="old thread", created_at=100)
        idx.thread_create(thread_id="new", bot="terry", user_id=1,
                          title="new thread", created_at=200)
        idx.thread_create(thread_id="newest", bot="terry", user_id=1,
                          title="newest", created_at=300)
        rows = idx.thread_list(bot="terry", user_id=1,
                               include_archived=False, limit=10)
        ids = [r["id"] for r in rows]
        assert ids == ["newest", "new", "old"]
    finally:
        idx.close()


def test_thread_list_excludes_archived_by_default(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.thread_create(thread_id="alive", bot="terry", user_id=1,
                          title="alive", created_at=100)
        idx.thread_create(thread_id="dead", bot="terry", user_id=1,
                          title="dead", created_at=200)
        idx.thread_archive(thread_id="dead", archived_at=300)
        # Default: archived hidden.
        rows = idx.thread_list(bot="terry", user_id=1,
                               include_archived=False, limit=10)
        assert [r["id"] for r in rows] == ["alive"]
        # Explicit include: both come back.
        rows_all = idx.thread_list(bot="terry", user_id=1,
                                   include_archived=True, limit=10)
        assert {r["id"] for r in rows_all} == {"alive", "dead"}
    finally:
        idx.close()


def test_thread_set_title_updates_in_place(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.thread_create(thread_id="t1", bot="terry", user_id=1,
                          title="initial", created_at=100)
        idx.thread_set_title(thread_id="t1", title="renamed by user")
        assert idx.thread_get(thread_id="t1")["title"] == "renamed by user"
    finally:
        idx.close()


def test_entity_page_upsert_creates_then_returns_prior(tmp_path: Path) -> None:
    """First upsert -> prior_existed=False; second -> prior truth returned."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        first = idx.entity_page_upsert(
            slug="kraken", kind="thing", title="Kraken",
            compiled_truth="A Drake capital ship.",
            timeline_entry="2026-04-29: first mention",
            now_epoch=1000,
        )
        assert first == {"prior_compiled_truth": "", "prior_existed": False}

        second = idx.entity_page_upsert(
            slug="kraken", kind="thing", title="Kraken",
            compiled_truth="A Drake-class capital ship with a hangar deck.",
            timeline_entry="2026-04-29: refined description",
            now_epoch=2000,
        )
        assert second["prior_existed"] is True
        assert second["prior_compiled_truth"] == "A Drake capital ship."

        page = idx.entity_page_get("kraken")
        assert page is not None
        assert page["compiled_truth"] == (
            "A Drake-class capital ship with a hangar deck."
        )
        # Timeline is append-only.
        assert "first mention" in page["timeline"]
        assert "refined description" in page["timeline"]
        assert page["last_mentioned_at"] == 2000
    finally:
        idx.close()


def test_entity_page_upsert_empty_truth_preserves_prior(tmp_path: Path) -> None:
    """Passing empty compiled_truth means 'just append timeline'."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.entity_page_upsert(
            slug="penguin", kind="person", title="Penguin",
            compiled_truth="The owner.", timeline_entry="initial entry",
            now_epoch=100,
        )
        idx.entity_page_upsert(
            slug="penguin", kind="person", title="Penguin",
            compiled_truth="",  # do not overwrite
            timeline_entry="another mention",
            now_epoch=200,
        )
        page = idx.entity_page_get("penguin")
        assert page["compiled_truth"] == "The owner."
        assert "initial entry" in page["timeline"]
        assert "another mention" in page["timeline"]
    finally:
        idx.close()


def test_entity_page_search_finds_by_title_or_truth(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.entity_page_upsert(
            slug="kraken", kind="thing", title="Kraken",
            compiled_truth="A Drake capital ship in Star Citizen.",
            timeline_entry="", now_epoch=100,
        )
        idx.entity_page_upsert(
            slug="ai-team", kind="project", title="Ai-Team",
            compiled_truth="The bots and gateway repo.",
            timeline_entry="", now_epoch=200,
        )
        hits = idx.entity_page_search("Kraken", limit=10)
        assert any(h["id"] == "kraken" for h in hits)
        truth_hits = idx.entity_page_search("gateway", limit=10)
        assert any(h["id"] == "ai-team" for h in truth_hits)
    finally:
        idx.close()


def test_entity_page_get_returns_none_for_missing(tmp_path: Path) -> None:
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        assert idx.entity_page_get("never-mentioned") is None
    finally:
        idx.close()


def test_search_chat_or_recovers_loose_tokens(tmp_path: Path) -> None:
    """Regression: a recall query 'multiplication answer' must surface
    a row reading '17 times 23 is 391' even though the row contains
    neither 'multiplication' nor 'answer'. Pre-fix this returned 0 hits
    because AND-implicit FTS required every token in the same row."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=42,
            role="user", content="What's 17 times 23?",
            created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=42,
            role="assistant", content="17 times 23 is 391.",
            created_at=1_001,
        )
        hits = idx.search_chat(
            bot="terry", user_id=42,
            query_text="17 23 multiplication answer",
            limit=8, thread_id="default",
        )
        assert hits, "OR semantics must rank loose token matches"
        contents = " ".join(h["content"] for h in hits)
        assert "391" in contents
    finally:
        idx.close()


def test_search_chat_filters_by_user_and_bot(tmp_path: Path) -> None:
    """OR semantics must not bypass the bot/user_id audience clamp."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="assistant", content="kraken note for user 1",
            created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=2,
            role="assistant", content="kraken note for user 2",
            created_at=1_001,
        )
        hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken", limit=8,
        )
        assert len(hits) == 1
        assert hits[0]["user_id"] == 1
    finally:
        idx.close()


def test_search_chat_isolates_threads(tmp_path: Path) -> None:
    """thread_id filter must scope results to that thread only.
    Important for the upcoming threads UX: forking a conversation
    cannot leak rows from the parent thread into recall on the
    child thread, and archived threads stay quiet."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="thread-a", bot="terry", user_id=1,
            role="user", content="alpha kraken in thread A",
            created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="thread-b", bot="terry", user_id=1,
            role="user", content="beta kraken in thread B",
            created_at=1_001,
        )
        a_hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken",
            thread_id="thread-a", limit=8,
        )
        assert len(a_hits) == 1
        assert a_hits[0]["thread_id"] == "thread-a"
        assert "alpha" in a_hits[0]["content"]

        b_hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken",
            thread_id="thread-b", limit=8,
        )
        assert len(b_hits) == 1
        assert b_hits[0]["thread_id"] == "thread-b"
    finally:
        idx.close()


def test_search_chat_no_thread_id_spans_all_threads(tmp_path: Path) -> None:
    """When thread_id is None the search is global across the user's
    threads. Lets 'what did we say about X across all threads' work."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        for thread, content in [
            ("thread-a", "kraken note one"),
            ("thread-b", "kraken note two"),
            ("thread-c", "kraken note three"),
        ]:
            idx.chat_log_append(
                thread_id=thread, bot="terry", user_id=1,
                role="assistant", content=content, created_at=1_000,
            )
        hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken", limit=8,
        )
        thread_ids = sorted(h["thread_id"] for h in hits)
        assert thread_ids == ["thread-a", "thread-b", "thread-c"]
    finally:
        idx.close()


def test_search_chat_isolates_bots(tmp_path: Path) -> None:
    """Two bots can hold the same user_id (the owner). search_chat
    must scope by bot so terry's history isn't surfaced when querying
    maggy and vice-versa."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="assistant", content="terry-side kraken",
            created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="maggy", user_id=1,
            role="assistant", content="maggy-side kraken",
            created_at=1_001,
        )
        terry_hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken", limit=8,
        )
        maggy_hits = idx.search_chat(
            bot="maggy", user_id=1, query_text="kraken", limit=8,
        )
        assert len(terry_hits) == 1
        assert terry_hits[0]["bot"] == "terry"
        assert "terry-side" in terry_hits[0]["content"]
        assert len(maggy_hits) == 1
        assert maggy_hits[0]["bot"] == "maggy"
    finally:
        idx.close()


def test_search_chat_bm25_ranks_relevant_higher(tmp_path: Path) -> None:
    """OR semantics still has to rank the row that matches more tokens
    above a row that matches fewer. Without BM25 the recall layer would
    flood the planner with noise."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="user",
            content="random off-topic chatter about kraken",
            created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="assistant",
            content="kraken star citizen drake capital ship",
            created_at=1_001,
        )
        hits = idx.search_chat(
            bot="terry", user_id=1,
            query_text="kraken star citizen ship",
            limit=8,
        )
        assert len(hits) == 2
        # Row that matches 4 tokens beats the row that matches 1.
        assert "star citizen" in hits[0]["content"]
    finally:
        idx.close()


def test_search_chat_respects_limit(tmp_path: Path) -> None:
    """Limit must be honoured even when many rows match — prevents
    chat_recall from blowing the planner's context budget."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        for i in range(10):
            idx.chat_log_append(
                thread_id="default", bot="terry", user_id=1,
                role="user", content=f"kraken sighting {i}",
                created_at=1_000 + i,
            )
        hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken", limit=3,
        )
        assert len(hits) == 3
    finally:
        idx.close()


def test_search_chat_returns_role_and_metadata(tmp_path: Path) -> None:
    """Both user and assistant rows must round-trip with role + ts —
    chat_recall renders them as alternating turns and needs both."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="user", content="kraken question from user",
            turn_id="turn-001", created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="assistant", content="kraken answer from terry",
            turn_id="turn-001", created_at=1_001,
        )
        hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken", limit=8,
        )
        roles = sorted(h["role"] for h in hits)
        assert roles == ["assistant", "user"]
        for h in hits:
            assert h["turn_id"] == "turn-001"
            assert isinstance(h["created_at"], int)
            assert h["pinned"] is False
            assert h["parent_id"] is None
    finally:
        idx.close()


def test_chat_log_append_returns_increasing_ids(tmp_path: Path) -> None:
    """SQLite AUTOINCREMENT contract — chat_log rowids must be
    monotonically increasing so 'newest first' ordering works."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        ids = [
            idx.chat_log_append(
                thread_id="default", bot="terry", user_id=1,
                role="user", content=f"turn {i}", created_at=1_000 + i,
            )
            for i in range(5)
        ]
        assert ids == sorted(ids)
        assert len(set(ids)) == 5
    finally:
        idx.close()


def test_chat_log_append_round_trips_optional_fields(tmp_path: Path) -> None:
    """turn_id + parent_id must survive insert→select intact —
    forking depends on parent_id pointing back into the source thread."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        first_id = idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="user", content="kraken parent",
            turn_id="turn-parent", created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="assistant", content="kraken child",
            turn_id="turn-child", parent_id=first_id,
            created_at=1_001,
        )
        hits = idx.search_chat(
            bot="terry", user_id=1, query_text="kraken child", limit=8,
        )
        child = next(h for h in hits if h["role"] == "assistant")
        assert child["parent_id"] == first_id
        assert child["turn_id"] == "turn-child"
    finally:
        idx.close()


def test_search_chat_handles_empty_and_whitespace_queries(tmp_path: Path) -> None:
    """Empty / whitespace-only / pure-punctuation queries must return
    an empty list rather than blowing up FTS5."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="user", content="kraken", created_at=1_000,
        )
        for q in ["", "   ", "!!!", "...---..."]:
            hits = idx.search_chat(
                bot="terry", user_id=1, query_text=q, limit=8,
            )
            assert hits == [], f"query {q!r} should yield no hits"
    finally:
        idx.close()


# ---- Finding 5: chat_log_clear --------------------------------------------


def test_chat_log_clear_removes_rows_for_user(tmp_path: Path) -> None:
    """Finding 5: chat_log_clear(bot, user_id) must delete all chat_log
    rows owned by that (bot, user_id) pair, so a MemoryStore.reset
    cannot leave chat history visible after the reset.
    """
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="user", content="private message A", created_at=1_000,
        )
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=1,
            role="assistant", content="reply A", created_at=1_001,
        )
        # Different user — must NOT be deleted.
        idx.chat_log_append(
            thread_id="default", bot="terry", user_id=2,
            role="user", content="user 2 message", created_at=1_002,
        )

        deleted = idx.chat_log_clear(bot="terry", user_id=1)
        assert deleted == 2, f"expected 2 deleted, got {deleted}"

        # User 1 rows gone.
        hits = idx.search_chat(bot="terry", user_id=1,
                               query_text="private", limit=10)
        assert hits == [], "user 1 rows should be gone after clear"

        # User 2 rows intact.
        hits2 = idx.search_chat(bot="terry", user_id=2,
                                query_text="user", limit=10)
        assert hits2, "user 2 rows must survive"
    finally:
        idx.close()


def test_chat_log_clear_noop_when_no_rows(tmp_path: Path) -> None:
    """chat_log_clear on a user with no rows returns 0 and doesn't
    raise — idempotent when called multiple times."""
    idx = VaultIndex.open(tmp_path / "vault.db", dimension=8)
    try:
        deleted = idx.chat_log_clear(bot="terry", user_id=99)
        assert deleted == 0
        # Second call also returns 0 without raising.
        deleted2 = idx.chat_log_clear(bot="terry", user_id=99)
        assert deleted2 == 0
    finally:
        idx.close()


def test_entity_page_relationships_round_trip(tmp_path: Path) -> None:
    """Phase 3 (#456): relationships JSON column round-trips through
    entity_page_upsert + entity_page_get with EXTRACTED / INFERRED /
    AMBIGUOUS confidence labels preserved."""
    db = tmp_path / "v.db"
    idx = VaultIndex.open(db, dimension=8)
    try:
        edges = [
            {"target_slug": "drake-interplanetary",
             "label": "manufactured_by", "confidence": "EXTRACTED"},
            {"target_slug": "user-org",
             "label": "is_flagship_of", "confidence": "INFERRED"},
            {"target_slug": "rumored-ship",
             "label": "supersedes", "confidence": "AMBIGUOUS"},
        ]
        idx.entity_page_upsert(
            slug="kraken", kind="thing", title="Kraken",
            compiled_truth="Drake heavy carrier.",
            timeline_entry="t0 mention",
            relationships=edges,
            now_epoch=1_700_000_000,
        )
        page = idx.entity_page_get("kraken")
        assert page is not None
        assert page["relationships"] == edges
    finally:
        idx.close()


def test_entity_page_relationships_default_empty(tmp_path: Path) -> None:
    """Pre-Phase-3 callers that don't supply relationships get an empty
    list rather than None or a stringified `[]`."""
    db = tmp_path / "v.db"
    idx = VaultIndex.open(db, dimension=8)
    try:
        idx.entity_page_upsert(
            slug="penguin", kind="person", title="Penguin (cat)",
            compiled_truth="user's cat",
            timeline_entry="",
            now_epoch=1_700_000_000,
        )
        page = idx.entity_page_get("penguin")
        assert page is not None
        assert page["relationships"] == []
    finally:
        idx.close()


def test_entity_page_relationships_invalid_json_skipped(tmp_path: Path) -> None:
    """A row whose `relationships` column was hand-edited to malformed
    JSON must NOT crash entity_page_get — it returns an empty list and
    logs a warning."""
    db = tmp_path / "v.db"
    idx = VaultIndex.open(db, dimension=8)
    try:
        idx.entity_page_upsert(
            slug="brokey", kind="concept", title="Brokey",
            compiled_truth="x", timeline_entry="",
            now_epoch=1_700_000_000,
        )
        idx._conn.execute(
            "UPDATE entity_page SET relationships = '{not json' WHERE id = ?",
            ("brokey",),
        )
        idx._conn.commit()
        page = idx.entity_page_get("brokey")
        assert page is not None
        assert page["relationships"] == []
    finally:
        idx.close()
