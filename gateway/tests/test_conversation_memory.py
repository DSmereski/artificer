"""Tests for the M5.2 tiered conversation memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.conversation_memory import (
    ConversationMemory, MemoryStore, refresh_summary_async,
)


def test_default_memory_empty(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(99)
    assert mem.user_id == 99
    assert mem.bot == "terry"
    assert mem.mid_summary == ""
    assert mem.turn_count == 0


def test_apply_summary_persists(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(
        user_id=42,
        summary="user is debugging a memory leak",
        open_tasks=["fix the leak"],
        decisions=["use weakref"],
        user_facts=["user prefers vim"],
    )
    # Reload.
    store2 = MemoryStore(tmp_path, bot="terry")
    mem = store2.get(42)
    assert "memory leak" in mem.mid_summary
    assert "fix the leak" in mem.mid_open_tasks
    assert "user prefers vim" in mem.mid_user_facts


def test_apply_summary_keeps_prior_when_new_is_empty(tmp_path):
    """An empty summary from a flaky LLM run must NOT wipe the prior.

    Regression guard for plan §1.2: each refresh used to overwrite
    `mid_summary` unconditionally, so any blank or truncated result
    would throw away accumulated context. The merge guard keeps the
    longer of (prior, new) when the new is empty or strictly shorter."""
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(
        user_id=1,
        summary="user is debugging a long-running pipeline issue",
        open_tasks=["repro"], decisions=[], user_facts=[],
    )
    prior = store.get(1).mid_summary
    # Empty summary — skipped.
    store.apply_summary(
        user_id=1, summary="",
        open_tasks=["repro"], decisions=[], user_facts=[],
    )
    assert store.get(1).mid_summary == prior
    # Strictly shorter summary — also skipped.
    store.apply_summary(
        user_id=1, summary="user is debugging.",
        open_tasks=["repro"], decisions=[], user_facts=[],
    )
    assert store.get(1).mid_summary == prior


def test_apply_summary_keeps_prior_lists_when_new_are_empty(tmp_path):
    """Empty list payloads from a flaky summarizer must NOT wipe the
    prior open_tasks / decisions / user_facts. Symmetric to the
    summary-text guard above. Without this, a single bad refresh
    erases multi-turn context (Star-Citizen-ship-list drift in the
    2026-04-26..05-01 prod review)."""
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(
        user_id=1, summary="rich prior",
        open_tasks=["finish the gallery refactor", "ship the installer"],
        decisions=["use SQLite + FTS5"],
        user_facts=["user prefers concise replies"],
    )
    # Empty lists — must be ignored, prior preserved.
    store.apply_summary(
        user_id=1, summary="rich prior, plus a new line",
        open_tasks=[], decisions=[], user_facts=[],
    )
    mem = store.get(1)
    assert mem.mid_open_tasks == [
        "finish the gallery refactor", "ship the installer",
    ]
    assert mem.mid_decisions == ["use SQLite + FTS5"]
    assert mem.mid_user_facts == ["user prefers concise replies"]


def test_apply_summary_replaces_when_new_is_longer(tmp_path):
    """Happy path: a longer summary (the summarizer extended the prior)
    replaces the old one."""
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(
        user_id=1, summary="short",
        open_tasks=[], decisions=[], user_facts=[],
    )
    extended = "short, plus extended context about the pipeline"
    store.apply_summary(
        user_id=1, summary=extended,
        open_tasks=[], decisions=[], user_facts=[],
    )
    assert store.get(1).mid_summary == extended


# ---- Finding 4: 0.8x ratio guard ------------------------------------------


def test_apply_summary_rejects_summary_at_exactly_0_8x_ratio(tmp_path):
    """Finding 4: the old guard used len(new) >= len(prior), meaning any
    new summary even 1 char shorter was rejected. This over-filtered
    legitimate refreshes where the LLM produced a slightly tighter
    (but substantively complete) re-summary.

    New guard: accept new summary when len(new) >= 0.8 * len(prior).

    Boundary at exactly 0.8x: new length == floor(0.8 * prior) should
    be accepted (just meets the threshold).
    """
    prior = "a" * 100
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, summary=prior,
                        open_tasks=[], decisions=[], user_facts=[])

    # new is exactly 80 chars — exactly at the 0.8x boundary.
    new_at_boundary = "b" * 80
    store.apply_summary(user_id=1, summary=new_at_boundary,
                        open_tasks=[], decisions=[], user_facts=[])
    assert store.get(1).mid_summary == new_at_boundary, (
        "summary at exactly 0.8x prior length should be accepted"
    )


def test_apply_summary_rejects_summary_just_below_0_8x_ratio(tmp_path):
    """A new summary just below 0.8x should be rejected (prior kept)."""
    prior = "a" * 100
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, summary=prior,
                        open_tasks=[], decisions=[], user_facts=[])

    # 79 chars is just below 80 (0.8 * 100), so should be rejected.
    new_too_short = "b" * 79
    store.apply_summary(user_id=1, summary=new_too_short,
                        open_tasks=[], decisions=[], user_facts=[])
    assert store.get(1).mid_summary == prior, (
        "summary just below 0.8x prior should be rejected"
    )


def test_apply_summary_accepts_summary_just_above_0_8x_ratio(tmp_path):
    """A new summary just above 0.8x should be accepted."""
    prior = "a" * 100
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, summary=prior,
                        open_tasks=[], decisions=[], user_facts=[])

    # 81 chars is above 80 (0.8 * 100), so should be accepted.
    new_just_above = "b" * 81
    store.apply_summary(user_id=1, summary=new_just_above,
                        open_tasks=[], decisions=[], user_facts=[])
    assert store.get(1).mid_summary == new_just_above, (
        "summary just above 0.8x prior should be accepted"
    )


@pytest.mark.asyncio
async def test_refresh_summary_async_forwards_prior_summary(tmp_path):
    """`refresh_summary_async` MUST pass the existing mid_summary into
    the helper as `prior_summary` so the summarizer can extend it.
    Without this, every refresh starts from scratch."""
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(
        user_id=1, summary="prior recap to extend",
        open_tasks=[], decisions=[], user_facts=[],
    )

    captured: dict = {}

    class _Result:
        output = {"summary": "prior recap to extend, plus new bits",
                  "open_tasks": [], "decisions": [], "user_facts": []}
        error = None

    class _Helper:
        async def invoke(self, task):
            captured["inputs"] = task.inputs
            return _Result()

    await refresh_summary_async(
        store, user_id=1,
        messages=[{"role": "user", "content": "hi"}],
        summarizer_helper=_Helper(),
    )
    assert captured["inputs"]["prior_summary"] == "prior recap to extend"


def test_increment_turn_persists_count(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    for _ in range(3):
        store.increment_turn(99)
    assert store.get(99).turn_count == 3


def test_needs_refresh_at_threshold(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.increment_turn(1)
    assert store.needs_refresh(mem) is False
    for _ in range(5):
        mem = store.increment_turn(1)
    assert store.needs_refresh(mem) is True


def test_render_for_planner(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(
        user_id=1,
        summary="working on the hive coordinator",
        open_tasks=["finish M5"],
        decisions=["keep schema permissive"],
        user_facts=["user is named Penguin"],
    )
    mem = store.get(1)
    out = mem.render_for_planner()
    assert "hive coordinator" in out
    assert "finish M5" in out
    assert "Penguin" in out


def test_reset_drops_disk_file(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, summary="x",
                        open_tasks=[], decisions=[], user_facts=[])
    sidecar = tmp_path / "1" / "default.memory.json"
    assert sidecar.is_file()
    store.reset(1)
    assert not sidecar.is_file()


def test_reset_with_none_drops_all_threads(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, thread_id="default", summary="x",
                        open_tasks=[], decisions=[], user_facts=[])
    store.apply_summary(user_id=1, thread_id="alt", summary="y",
                        open_tasks=[], decisions=[], user_facts=[])
    assert (tmp_path / "1" / "default.memory.json").is_file()
    assert (tmp_path / "1" / "alt.memory.json").is_file()
    store.reset(1, thread_id=None)
    assert not (tmp_path / "1" / "default.memory.json").is_file()
    assert not (tmp_path / "1" / "alt.memory.json").is_file()


def test_reset_calls_chat_log_clear_callback(tmp_path):
    """Finding 5 (security): MemoryStore.reset must call the optional
    on_chat_log_clear callback (keyed by user_id) so that the vault
    daemon's chat_log rows are wiped alongside the sidecar. Without this,
    sensitive chat history persists in SQLite after a reset, leaking
    prior-conversation context into the new session.

    The callback is injected rather than hard-wired to keep MemoryStore
    decoupled from VaultClient.
    """
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, summary="secret data",
                        open_tasks=[], decisions=[], user_facts=[])

    calls: list[tuple] = []

    def fake_clear(user_id: int, bot: str) -> None:
        calls.append((user_id, bot))

    store.reset(1, on_chat_log_clear=fake_clear)
    assert calls == [(1, "terry")], (
        "reset must invoke on_chat_log_clear(user_id, bot) once"
    )


def test_reset_chat_log_callback_not_called_when_not_provided(tmp_path):
    """Backward compat: reset without on_chat_log_clear must not raise."""
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, summary="x",
                        open_tasks=[], decisions=[], user_facts=[])
    # Must not raise even without the callback.
    store.reset(1)


def test_reset_sweeps_legacy_sidecar_even_for_per_thread(tmp_path):
    """If a stray pre-Phase-2 `<user_id>.memory.json` exists alongside
    the new layout, a per-thread reset MUST also drop it. Otherwise the
    next get() re-migrates it back into `default.memory.json`, undoing
    the reset the user just asked for."""
    legacy = tmp_path / "1.memory.json"
    legacy.write_text(
        '{"mid_summary":"stale","mid_open_tasks":[],"mid_decisions":[],'
        '"mid_user_facts":[],"mid_summary_at_turn":0,"long_digest":"",'
        '"turn_count":0}',
        encoding="utf-8",
    )
    store = MemoryStore(tmp_path, bot="terry")
    store.reset(1, thread_id="default")
    assert not legacy.exists()
    # And the next get returns a fresh memory, not the stale one.
    assert store.get(1).mid_summary == ""


def test_legacy_sidecar_migrates_on_first_read(tmp_path):
    """Existing pre-Phase-2 deploys keep their accumulated context:
    `<root>/<user_id>.memory.json` is moved to
    `<root>/<user_id>/default.memory.json` on the first get()."""
    legacy = tmp_path / "7.memory.json"
    legacy.write_text(
        '{"mid_summary":"old context","mid_open_tasks":[],"mid_decisions":[],'
        '"mid_user_facts":[],"mid_summary_at_turn":3,"long_digest":"",'
        '"turn_count":3}',
        encoding="utf-8",
    )
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(7)
    assert mem.mid_summary == "old context"
    assert mem.turn_count == 3
    # Legacy file is gone, new layout file exists.
    assert not legacy.exists()
    assert (tmp_path / "7" / "default.memory.json").is_file()


def test_per_thread_isolation(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_summary(user_id=1, thread_id="default", summary="thread A",
                        open_tasks=[], decisions=[], user_facts=[])
    store.apply_summary(user_id=1, thread_id="other", summary="thread B",
                        open_tasks=[], decisions=[], user_facts=[])
    assert store.get(1, "default").mid_summary == "thread A"
    assert store.get_for_thread(1, "other").mid_summary == "thread B"


# ---------------------------------------------------------------- async refresh


class _FakeSummarizer:
    def __init__(self, output) -> None:
        self.output = output
        self.invoked = 0

    async def invoke(self, task):
        self.invoked += 1
        from gateway.helpers.base import HelperResult
        return HelperResult(
            role="summarizer", model_id="qwen-8b",
            output=self.output,
        )


@pytest.mark.asyncio
async def test_refresh_summary_async_writes(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    summarizer = _FakeSummarizer({
        "summary": "a recap",
        "open_tasks": ["task1"],
        "decisions": ["dec1"],
        "user_facts": ["fact1"],
    })
    await refresh_summary_async(
        store, user_id=1,
        messages=[{"role": "user", "content": "hi"}],
        summarizer_helper=summarizer,
    )
    assert summarizer.invoked == 1
    mem = store.get(1)
    assert mem.mid_summary == "a recap"
    assert mem.mid_open_tasks == ["task1"]


@pytest.mark.asyncio
async def test_refresh_summary_async_handles_helper_error(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")

    class _BrokenSummarizer:
        async def invoke(self, task):
            from gateway.helpers.base import HelperResult
            return HelperResult(role="summarizer", model_id="x", error="boom")

    await refresh_summary_async(
        store, user_id=1, messages=[],
        summarizer_helper=_BrokenSummarizer(),
    )
    # No summary persisted on error.
    mem = store.get(1)
    assert mem.mid_summary == ""


@pytest.mark.asyncio
async def test_refresh_summary_async_no_helper_is_noop(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    await refresh_summary_async(
        store, user_id=1, messages=[], summarizer_helper=None,
    )
    assert store.get(1).mid_summary == ""


# ---------------------------------------------------------------- long_digest


def test_needs_long_digest_period(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 100   # 5 * 20 = 100, the configured period
    mem.mid_summary = "something to compress"
    # Summarizer ran this cycle so mid_summary_at_turn matches turn_count.
    mem.mid_summary_at_turn = 100
    assert store.needs_long_digest(mem) is True


def test_needs_long_digest_off_period(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 50    # not a multiple of 100
    mem.mid_summary = "x"
    assert store.needs_long_digest(mem) is False


def test_needs_long_digest_skipped_when_empty(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 100
    # Both summary fields empty → nothing to compress.
    assert store.needs_long_digest(mem) is False


def test_needs_long_digest_false_when_mid_summary_not_advanced(tmp_path):
    """Finding 1: turn_count hit the period boundary but mid_summary_at_turn
    was NOT updated since the last digest — the summarizer hasn't run yet
    (e.g. it was skipped due to an error or the period fired before the
    first refresh). Guard must return False so we don't compress a stale
    mid_summary into long_digest.

    Concretely: turn_count is at the period but mid_summary_at_turn has
    not changed since turn_count reached the boundary — digest gate should
    be False.
    """
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 100
    mem.mid_summary = "something to compress"
    # mid_summary_at_turn still at its default 0 — the summary was NOT
    # refreshed this cycle (it was written at turn 0, long before tc=100).
    mem.mid_summary_at_turn = 0
    # With the guard in place this should be False; without the guard it
    # would be True (the existing period + non-empty check passes).
    assert store.needs_long_digest(mem) is False


def test_needs_long_digest_true_when_mid_summary_freshly_refreshed(tmp_path):
    """Positive counterpart to the stale-summary test: when the summary
    WAS refreshed this cycle (mid_summary_at_turn equals turn_count),
    the gate must return True so long_digest compression actually fires."""
    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 100
    mem.mid_summary = "something to compress"
    # Summarizer just ran — mid_summary_at_turn is current.
    mem.mid_summary_at_turn = 100
    assert store.needs_long_digest(mem) is True


def test_apply_long_digest_caps_length(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    big = "z" * 5000
    mem = store.apply_long_digest(user_id=1, digest=big)
    assert len(mem.long_digest) == store.LONG_DIGEST_CHAR_CAP
    # Reload from disk to make sure it persisted.
    mem2 = MemoryStore(tmp_path, bot="terry").get(1)
    assert mem2.long_digest == mem.long_digest


def test_apply_long_digest_empty_is_noop(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_long_digest(user_id=1, digest="prior")
    store.apply_long_digest(user_id=1, digest="")  # should not wipe
    assert store.get(1).long_digest == "prior"


def test_render_for_planner_includes_long_digest(tmp_path):
    store = MemoryStore(tmp_path, bot="terry")
    store.apply_long_digest(user_id=1, digest="- user is named Penguin")
    store.apply_summary(
        user_id=1, summary="working on memory", open_tasks=[],
        decisions=[], user_facts=[],
    )
    out = store.get(1).render_for_planner()
    assert "Standing facts" in out
    assert "Penguin" in out
    assert "Conversation so far" in out


@pytest.mark.asyncio
async def test_refresh_fires_long_digest_compression(tmp_path):
    """When turn_count hits the period, the refresh path invokes the
    summarizer a second time in compression mode and writes its
    output to long_digest."""
    store = MemoryStore(tmp_path, bot="terry")
    # Bring the store to one turn before the period so the refresh
    # increment lands us exactly at 100.
    mem = store.get(1)
    mem.turn_count = 100
    mem.mid_summary = "lots of conversation history"
    store.save(mem)

    invocations: list[dict] = []

    class _Summarizer:
        async def invoke(self, task):
            invocations.append(dict(task.inputs))
            from gateway.helpers.base import HelperResult
            if task.inputs.get("compress_to_long_digest"):
                return HelperResult(
                    role="summarizer", model_id="x",
                    output={"summary": "- user prefers concise replies",
                            "open_tasks": [], "decisions": [],
                            "user_facts": []},
                )
            return HelperResult(
                role="summarizer", model_id="x",
                output={"summary": "lots of conversation history extended",
                        "open_tasks": [], "decisions": [],
                        "user_facts": []},
            )

    await refresh_summary_async(
        store, user_id=1,
        messages=[{"role": "user", "content": "x"}],
        summarizer_helper=_Summarizer(),
    )

    assert len(invocations) == 2
    assert invocations[1]["compress_to_long_digest"] is True
    assert "user prefers concise" in store.get(1).long_digest


@pytest.mark.asyncio
async def test_long_digest_empty_summary_logs_warning(tmp_path, caplog):
    """Production silent-failure pin: when the compress-mode call
    returns a valid envelope with an empty `summary` field,
    `apply_long_digest("")` is a no-op. Without an operator-visible
    log at this boundary the trigger fires every 100 turns and
    produces nothing — exactly what the production sidecar at
    user 3037965419 / tc=100 / long_digest='' showed on 2026-05-01."""
    import logging

    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 100
    mem.mid_summary = "lots of conversation history"
    store.save(mem)

    class _Summarizer:
        async def invoke(self, task):
            from gateway.helpers.base import HelperResult
            if task.inputs.get("compress_to_long_digest"):
                # Valid envelope, empty summary — the suspected
                # production failure mode.
                return HelperResult(
                    role="summarizer", model_id="x",
                    output={"summary": "", "open_tasks": [],
                            "decisions": [], "user_facts": []},
                )
            return HelperResult(
                role="summarizer", model_id="x",
                output={"summary": "lots of conversation history extended",
                        "open_tasks": [], "decisions": [],
                        "user_facts": []},
            )

    with caplog.at_level(logging.WARNING, logger="gateway.conversation_memory"):
        await refresh_summary_async(
            store, user_id=1,
            messages=[{"role": "user", "content": "x"}],
            summarizer_helper=_Summarizer(),
        )

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("empty summary" in r.getMessage() for r in warns), (
        f"expected 'empty summary' warning, got: {[r.getMessage() for r in warns]}"
    )
    # And digest stays empty (apply_long_digest no-op preserved).
    assert store.get(1).long_digest == ""


@pytest.mark.asyncio
async def test_long_digest_helper_error_logs_warning(tmp_path, caplog):
    """Pin the WARNING (was INFO) on helper-reported error path so
    silent compress-mode parse failures surface in gateway.log.err."""
    import logging

    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(1)
    mem.turn_count = 100
    mem.mid_summary = "history"
    store.save(mem)

    class _Summarizer:
        async def invoke(self, task):
            from gateway.helpers.base import HelperResult
            if task.inputs.get("compress_to_long_digest"):
                return HelperResult(
                    role="summarizer", model_id="x",
                    output=None, error="schema validation failed",
                )
            return HelperResult(
                role="summarizer", model_id="x",
                output={"summary": "history extended", "open_tasks": [],
                        "decisions": [], "user_facts": []},
            )

    with caplog.at_level(logging.WARNING, logger="gateway.conversation_memory"):
        await refresh_summary_async(
            store, user_id=1,
            messages=[{"role": "user", "content": "x"}],
            summarizer_helper=_Summarizer(),
        )

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("compression failed" in r.getMessage() for r in warns)


# ---------------------------------------------------------------- fact_extractor


class _FakeFactExtractor:
    def __init__(self, output) -> None:
        self.output = output
        self.invoked = 0
        self.last_inputs: dict | None = None

    async def invoke(self, task):
        self.invoked += 1
        self.last_inputs = dict(task.inputs)
        from gateway.helpers.base import HelperResult
        return HelperResult(
            role="fact_extractor", model_id="x", output=self.output,
        )


@pytest.mark.asyncio
async def test_fact_extractor_appends_to_core_slots(tmp_path):
    """A successful fact-extractor delta lands in the right slots."""
    store = MemoryStore(tmp_path, bot="terry")
    summarizer = _FakeSummarizer({
        "summary": "ongoing conversation about memory",
        "open_tasks": [], "decisions": [], "user_facts": [],
    })
    extractor = _FakeFactExtractor({
        "user_facts_added": ["user runs three GPUs at home"],
        "preferences_added": ["user prefers terse replies"],
        "decisions_added": ["agreed to defer LangGraph migration"],
        "open_tasks_added": ["finish Phase 3 plan"],
        "open_tasks_resolved": ["shipped chat_log table"],
        "entities_mentioned": ["phase-3", "langgraph"],
    })

    await refresh_summary_async(
        store, user_id=7, thread_id="default",
        messages=[{"role": "user", "content": "hello there"}],
        summarizer_helper=summarizer,
        fact_extractor_helper=extractor,
    )

    assert extractor.invoked == 1
    mem = store.get(7, "default")
    assert "three GPUs" in mem.core_slots["user_profile"].content
    assert "terse" in mem.core_slots["preferences"].content
    decisions = mem.core_slots["recent_decisions"].content
    assert "defer LangGraph" in decisions
    # Resolved tasks land in recent_decisions with [done] prefix.
    assert "[done]" in decisions and "shipped chat_log" in decisions
    assert "finish Phase 3" in mem.core_slots["open_tasks"].content


@pytest.mark.asyncio
async def test_fact_extractor_failure_does_not_break_summary(tmp_path):
    """If fact extraction errors, the summary still persisted."""
    store = MemoryStore(tmp_path, bot="terry")
    summarizer = _FakeSummarizer({
        "summary": "valid recap",
        "open_tasks": [], "decisions": [], "user_facts": [],
    })

    class _BrokenExtractor:
        async def invoke(self, task):
            raise RuntimeError("model unreachable")

    await refresh_summary_async(
        store, user_id=8, thread_id="default",
        messages=[{"role": "user", "content": "x"}],
        summarizer_helper=summarizer,
        fact_extractor_helper=_BrokenExtractor(),
    )
    # Summarizer half still landed.
    assert store.get(8, "default").mid_summary == "valid recap"
    # No core slots populated on extractor crash.
    assert store.get(8, "default").core_slots == {}


@pytest.mark.asyncio
async def test_fact_extractor_skipped_when_helper_is_none(tmp_path):
    """The fact_extractor branch is opt-in — backward compat for callers
    that don't pass a helper (existing tests, scripted summaries)."""
    store = MemoryStore(tmp_path, bot="terry")
    summarizer = _FakeSummarizer({
        "summary": "ok", "open_tasks": [],
        "decisions": [], "user_facts": [],
    })
    await refresh_summary_async(
        store, user_id=9,
        messages=[{"role": "user", "content": "x"}],
        summarizer_helper=summarizer,
        # no fact_extractor_helper passed
    )
    assert store.get(9).mid_summary == "ok"
    assert store.get(9).core_slots == {}


@pytest.mark.asyncio
async def test_fact_extractor_forwards_prior_summary(tmp_path):
    """The extractor receives the just-applied summary so it can use it
    for context (e.g. avoiding contradictions with prior turns)."""
    store = MemoryStore(tmp_path, bot="terry")
    summarizer = _FakeSummarizer({
        "summary": "user is debugging Hive memory",
        "open_tasks": [], "decisions": [], "user_facts": [],
    })
    extractor = _FakeFactExtractor({})

    await refresh_summary_async(
        store, user_id=10,
        messages=[{"role": "user", "content": "hi"}],
        summarizer_helper=summarizer,
        fact_extractor_helper=extractor,
    )
    assert extractor.last_inputs is not None
    assert "Hive memory" in extractor.last_inputs.get("prior_summary", "")


# ---------------------------------------------------------------- low-signal filter (#441)


def test_is_durable_fact_accepts_real_facts():
    """Real, evidence-based facts should pass through unchanged. The
    filter must NOT be so aggressive that it drops legitimate
    extractions — false positives are silently lost data."""
    from gateway.conversation_memory import is_durable_fact

    assert is_durable_fact("user runs a homelab with three GPUs")
    assert is_durable_fact("user's cat is named Penguin")
    assert is_durable_fact("user is allergic to pineapple")
    assert is_durable_fact("user picked option B for the wizard layout")


def test_is_durable_fact_rejects_cross_user_boilerplate():
    """The chat-log review (2026-05-01) found these exact strings
    appearing identically across multiple unrelated users' sidecars —
    strong signal of LLM-hallucinated default rather than evidence."""
    from gateway.conversation_memory import is_durable_fact

    assert not is_durable_fact("user prefers concise and natural responses")
    assert not is_durable_fact("user prefers concise responses")
    assert not is_durable_fact("user wants helpful responses")
    # Casing must not matter — LLM output is inconsistent.
    assert not is_durable_fact("USER PREFERS CONCISE RESPONSES")


def test_is_durable_fact_rejects_per_turn_ephemera():
    """Time-bound phrases ("currently", "this turn", "today") signal
    state that decays, not durable facts."""
    from gateway.conversation_memory import is_durable_fact

    assert not is_durable_fact(
        "user is currently on a carpet without an anti-static strap"
    )
    assert not is_durable_fact("user just typed a question about Star Citizen")
    assert not is_durable_fact("user wants the answer right now")
    assert not is_durable_fact("user agreed in this turn to defer the migration")
    assert not is_durable_fact("user is grounded today by touching the case")


def test_is_durable_fact_rejects_fragments():
    """Anything below the fragment threshold is almost always a
    truncated extraction — discard rather than persist."""
    from gateway.conversation_memory import is_durable_fact

    assert not is_durable_fact("")
    assert not is_durable_fact("   ")
    assert not is_durable_fact("hi")
    assert not is_durable_fact("ok")
    assert not is_durable_fact("foo bar")  # 7 chars, just under threshold


@pytest.mark.asyncio
async def test_fact_extractor_filters_low_signal_items(tmp_path):
    """End-to-end: the extractor returns a mix of durable facts and
    low-signal noise; only the durable items hit the slots."""
    store = MemoryStore(tmp_path, bot="terry")
    summarizer = _FakeSummarizer({
        "summary": "ongoing", "open_tasks": [],
        "decisions": [], "user_facts": [],
    })
    extractor = _FakeFactExtractor({
        "user_facts_added": [
            "user runs three GPUs at home",          # keeper
            "user is currently on a carpet",         # ephemera (drop)
            "user just said hi",                     # ephemera (drop)
        ],
        "preferences_added": [
            "user prefers concise responses",        # boilerplate (drop)
            "user prefers terse replies, no preamble",  # keeper
        ],
        "decisions_added": [],
        "open_tasks_added": [],
        "open_tasks_resolved": [],
    })

    await refresh_summary_async(
        store, user_id=11, thread_id="default",
        messages=[{"role": "user", "content": "hi"}],
        summarizer_helper=summarizer,
        fact_extractor_helper=extractor,
    )

    profile = store.get(11, "default").core_slots["user_profile"].content
    prefs = store.get(11, "default").core_slots["preferences"].content
    assert "three GPUs" in profile
    assert "carpet" not in profile
    assert "just said" not in profile
    assert "terse replies" in prefs
    assert "concise responses" not in prefs


# ---------------------------------------------------------------- plan §1.3/§1.4

def test_render_caps_runaway_mid_summary(tmp_path):
    """A degenerate summarizer run that bloats mid_summary must not
    blow up the planner prompt. render_for_planner clamps it to the
    most recent tail (plan §1.3)."""
    from gateway.conversation_memory import MID_SUMMARY_RENDER_CHAR_CAP

    store = MemoryStore(tmp_path, bot="terry")
    mem = store.get(7)
    mem.mid_summary = "OLD " * 3000 + "RECENT_MARKER"
    store.save(mem)
    rendered = store.get(7).render_for_planner()
    # The conversation-so-far section is bounded.
    assert len(rendered) < MID_SUMMARY_RENDER_CHAR_CAP + 500
    # The most recent content survives.
    assert "RECENT_MARKER" in rendered


def test_append_core_slot_drops_oldest_whole_lines(tmp_path):
    """Overflow drops whole oldest lines; the newest fact stays intact
    (plan §1.4). A left byte-slice would have severed it mid-line."""
    store = MemoryStore(tmp_path, bot="terry")
    limit = 60
    store.append_core_slot(
        user_id=3, name="preferences",
        content="old fact one", char_limit=limit,
    )
    store.append_core_slot(
        user_id=3, name="preferences",
        content="old fact two", char_limit=limit,
    )
    newest = "user prefers dark mode and terse replies"
    store.append_core_slot(
        user_id=3, name="preferences",
        content=newest, char_limit=limit,
    )
    slot = store.get(3).core_slots["preferences"].content
    assert len(slot) <= limit
    # Newest fact is preserved whole (not severed mid-line).
    assert newest in slot
    # Oldest line was dropped entirely, not partially.
    assert "fact one" not in slot
