"""Tests for the helpers extracted from `_hive_turn`.

The helpers each have one job, so the tests pin one assertion per
behaviour. Together they replace the implicit coverage that came
from end-to-end chat WS tests when everything lived in chat.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from gateway.hive_turn_helpers import (
    _generate_and_set_thread_title,
    build_turn_context,
    maybe_auto_title_thread,
    persist_hive_turn_history,
    publish_turn_done_notifications,
    record_turn_telemetry,
    schedule_summarizer_refresh,
)


# ---------------------------------------------------------------- shared fakes


@dataclass
class _FakeTurn:
    reply: str = "hello"
    blocked: bool = False
    error: str | None = None
    helpers_used: list[str] = field(default_factory=list)
    total_tokens: int = 0
    total_latency_ms: int = 0
    actions: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    helper_results: list = field(default_factory=list)
    planner_result: Any = None
    synth_result: Any = None
    critic_result: Any = None
    turn_id: str = "tk-test"


class _AppState:
    """Minimal duck-typed AppState. Every field accessed by hive_turn_helpers
    must be declared here (direct attribute access, no getattr fallbacks)."""
    def __init__(self, **overrides):
        # background_tasks must be a real set for track_background_task
        self.background_tasks: set = set()
        # build_turn_context fields
        self.image_build_store = None
        self.skill_registry = None
        self.memory_store_terry = None
        self.helpers: dict = {}
        # record_turn_telemetry
        self.turn_telemetry = None
        # record_turn_log
        self.turn_log_store = None
        # publish_turn_done_notifications
        self.event_bus = None
        self.ntfy = None
        # schedule_summarizer_refresh / persist_hive_turn_history
        self.adapters: dict = {}
        # index_hive_turn_to_chat_log
        self.vault_client = None
        for k, v in overrides.items():
            setattr(self, k, v)


# ---------------------------------------------------------------- build_turn_context


def test_build_turn_context_with_no_app_state_pieces():
    """All optional sources missing — context still builds with defaults."""
    state = _AppState()
    ctx = build_turn_context(
        state, user_id=1, text="hi", device_id="d", device_audience=None,
    )
    assert ctx.user_msg == "hi"
    assert ctx.user_id == 1
    assert ctx.history_digest == ""
    assert ctx.skills_digest == ""
    assert ctx.image_build is None
    assert ctx.suggested_skills == []


def test_build_turn_context_excludes_internal_helpers():
    """planner + synthesizer are pipeline stages — never visible to the
    planner as delegate-able helpers."""
    state = _AppState(helpers={
        "planner": object(), "synthesizer": object(),
        "researcher": object(), "librarian": object(),
    })
    ctx = build_turn_context(
        state, user_id=1, text="hi", device_id="d", device_audience=None,
    )
    assert "planner" not in ctx.available_helpers
    assert "synthesizer" not in ctx.available_helpers
    assert "researcher" in ctx.available_helpers
    assert "librarian" in ctx.available_helpers


# ---------------------------------------------------------------- telemetry


def test_record_turn_telemetry_no_op_when_disabled():
    state = _AppState()  # no turn_telemetry
    record_turn_telemetry(state, _FakeTurn(), device_id="d", text="hi")
    # Just ensure it didn't raise.


def test_record_turn_telemetry_records_when_enabled():
    class _FakeTel:
        def __init__(self): self.records = []
        def record(self, r): self.records.append(r)
    tel = _FakeTel()
    state = _AppState(turn_telemetry=tel)
    record_turn_telemetry(
        state,
        _FakeTurn(actions=[{"verb": "vault_learn"}, {"verb": "image_render"}]),
        device_id="device-12345", text="hi",
    )
    assert len(tel.records) == 1
    rec = tel.records[0]
    assert rec.bot == "terry"
    assert rec.actions == ["vault_learn", "image_render"]


# ---------------------------------------------------------------- notifications


@pytest.mark.asyncio
async def test_publish_skips_blocked_turn():
    """Blocked turns don't fire ntfy or event_bus."""
    class _Bus:
        def __init__(self): self.events = []
        def publish(self, e): self.events.append(e)
    bus = _Bus()
    state = _AppState(event_bus=bus)
    await publish_turn_done_notifications(
        state, _FakeTurn(blocked=True, reply="long enough reply ok"),
        device_id="d",
    )
    assert bus.events == []


@pytest.mark.asyncio
async def test_publish_skips_short_replies():
    """Replies ≤8 chars are noise (e.g. 'ok', 'sure')."""
    class _Bus:
        def __init__(self): self.events = []
        def publish(self, e): self.events.append(e)
    bus = _Bus()
    state = _AppState(event_bus=bus)
    await publish_turn_done_notifications(
        state, _FakeTurn(reply="ok"), device_id="d",
    )
    assert bus.events == []


@pytest.mark.asyncio
async def test_publish_emits_for_real_reply():
    class _Bus:
        def __init__(self): self.events = []
        def publish(self, e): self.events.append(e)
    bus = _Bus()
    state = _AppState(event_bus=bus)
    await publish_turn_done_notifications(
        state, _FakeTurn(reply="this is a meaningful reply"),
        device_id="d-1234",
    )
    assert len(bus.events) == 1
    assert bus.events[0]["type"] == "hive_turn_done"
    assert bus.events[0]["device_id"] == "d-1234"


# ---------------------------------------------------------------- persist history


def test_persist_skips_blocked_turn():
    class _LLM:
        def __init__(self): self.calls = []
        def record_turn(self, *args): self.calls.append(args)
    class _Adapter:
        _llm = _LLM()
    state = _AppState(adapters={"terry": _Adapter()})
    persist_hive_turn_history(
        state, _FakeTurn(blocked=True), user_id=1, text="hi",
    )
    assert state.adapters["terry"]._llm.calls == []


def test_persist_writes_for_clean_turn():
    class _LLM:
        def __init__(self): self.calls = []
        def record_turn(self, uid, msg, reply): self.calls.append((uid, msg, reply))
    class _Adapter:
        _llm = _LLM()
    adapter = _Adapter()
    state = _AppState(adapters={"terry": adapter})
    persist_hive_turn_history(
        state, _FakeTurn(reply="hello there"),
        user_id=42, text="hi",
    )
    assert adapter._llm.calls == [(42, "hi", "hello there")]


def test_persist_handles_missing_adapter():
    """No `terry` adapter, no LLM — silently no-op."""
    state = _AppState(adapters={})
    persist_hive_turn_history(
        state, _FakeTurn(reply="ok"), user_id=1, text="hi",
    )
    # No exception raised.


# ---------------------------------------------------------------- summarizer


def test_summarizer_no_op_when_threshold_not_crossed():
    class _Mem:
        turns_recorded = 0
        def increment_turn(self, uid, thread_id="default"):
            self.turns_recorded += 1
        def get(self, uid, thread_id="default"): return object()
        def needs_refresh(self, m): return False
    state = _AppState(memory_store_terry=_Mem(), helpers={"summarizer": object()})
    schedule_summarizer_refresh(
        state, _FakeTurn(reply="ok"), user_id=1, text="hi",
    )
    assert state.background_tasks == set()
    assert state.memory_store_terry.turns_recorded == 1


def test_summarizer_no_op_when_summarizer_helper_missing():
    """needs_refresh true but no summarizer → still no task."""
    class _Mem:
        def increment_turn(self, uid, thread_id="default"): pass
        def get(self, uid, thread_id="default"): return object()
        def needs_refresh(self, m): return True
    state = _AppState(memory_store_terry=_Mem(), helpers={})
    schedule_summarizer_refresh(
        state, _FakeTurn(reply="ok"), user_id=1, text="hi",
    )
    assert state.background_tasks == set()


# ---------------------------------------------------------------- auto-title (Phase 2.6)


@dataclass
class _MemEntry:
    """Just a `turn_count` carrier — that's all maybe_auto_title_thread reads."""
    turn_count: int = 0


class _FakeTitleMem:
    def __init__(self, turn_count: int):
        self._entry = _MemEntry(turn_count=turn_count)
    def get(self, uid, thread_id="default"):
        return self._entry


class _FakeVC:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
    async def thread_set_title(self, *, thread_id: str, title: str):
        self.calls.append((thread_id, title))


def test_auto_title_skips_when_turn_count_below_trigger():
    state = _AppState(
        memory_store_terry=_FakeTitleMem(turn_count=2),
        helpers={"summarizer": object()},
        vault_client=_FakeVC(),
    )
    maybe_auto_title_thread(
        state, bot="terry", user_id=1, text="hi", thread_id="t1",
    )
    assert state.background_tasks == set()


def test_auto_title_skips_when_turn_count_above_trigger():
    """Don't replace a real title once we're past the trigger turn."""
    state = _AppState(
        memory_store_terry=_FakeTitleMem(turn_count=4),
        helpers={"summarizer": object()},
        vault_client=_FakeVC(),
    )
    maybe_auto_title_thread(
        state, bot="terry", user_id=1, text="hi", thread_id="t1",
    )
    assert state.background_tasks == set()


def test_auto_title_skips_when_summarizer_helper_missing():
    state = _AppState(
        memory_store_terry=_FakeTitleMem(turn_count=3),
        helpers={},
        vault_client=_FakeVC(),
    )
    maybe_auto_title_thread(
        state, bot="terry", user_id=1, text="hi", thread_id="t1",
    )
    assert state.background_tasks == set()


def test_auto_title_skips_when_vault_client_missing():
    state = _AppState(
        memory_store_terry=_FakeTitleMem(turn_count=3),
        helpers={"summarizer": object()},
        vault_client=None,
    )
    maybe_auto_title_thread(
        state, bot="terry", user_id=1, text="hi", thread_id="t1",
    )
    assert state.background_tasks == set()


@pytest.mark.asyncio
async def test_auto_title_fires_at_trigger_turn():
    """At turn 3 the helper schedules a tracked background task."""
    class _NopSummarizer:
        async def invoke(self, task):
            from gateway.helpers.base import HelperResult
            return HelperResult(role="summarizer", model_id="x",
                                output={"summary": ""})
    state = _AppState(
        memory_store_terry=_FakeTitleMem(turn_count=3),
        helpers={"summarizer": _NopSummarizer()},
        vault_client=_FakeVC(),
    )
    maybe_auto_title_thread(
        state, bot="terry", user_id=1, text="hi", thread_id="t1",
    )
    assert len(state.background_tasks) == 1
    # Drain the task so pytest doesn't warn about un-awaited coroutines.
    await asyncio.gather(*state.background_tasks)


@pytest.mark.asyncio
async def test_generate_and_set_calls_thread_set_title():
    from gateway.helpers.base import HelperResult

    class _Summ:
        async def invoke(self, task):
            return HelperResult(
                role="summarizer", model_id="x",
                output={"summary": "Star Citizen Ship Talk"},
            )
    vc = _FakeVC()
    await _generate_and_set_thread_title(
        summarizer=_Summ(), vault_client=vc,
        thread_id="t1", messages=[{"role": "user", "content": "hi"}],
    )
    assert vc.calls == [("t1", "Star Citizen Ship Talk")]


@pytest.mark.asyncio
async def test_generate_and_set_skips_empty_summary():
    from gateway.helpers.base import HelperResult

    class _Summ:
        async def invoke(self, task):
            return HelperResult(role="summarizer", model_id="x",
                                output={"summary": "   "})
    vc = _FakeVC()
    await _generate_and_set_thread_title(
        summarizer=_Summ(), vault_client=vc,
        thread_id="t1", messages=[{"role": "user", "content": "hi"}],
    )
    assert vc.calls == []


@pytest.mark.asyncio
async def test_generate_and_set_skips_on_helper_error():
    from gateway.helpers.base import HelperResult

    class _Summ:
        async def invoke(self, task):
            return HelperResult(role="summarizer", model_id="x",
                                output={}, error="boom")
    vc = _FakeVC()
    await _generate_and_set_thread_title(
        summarizer=_Summ(), vault_client=vc,
        thread_id="t1", messages=[{"role": "user", "content": "hi"}],
    )
    assert vc.calls == []


@pytest.mark.asyncio
async def test_generate_and_set_swallows_summarizer_exception():
    class _Summ:
        async def invoke(self, task):
            raise RuntimeError("model down")
    vc = _FakeVC()
    # Must not raise.
    await _generate_and_set_thread_title(
        summarizer=_Summ(), vault_client=vc,
        thread_id="t1", messages=[{"role": "user", "content": "hi"}],
    )
    assert vc.calls == []


@pytest.mark.asyncio
async def test_generate_and_set_truncates_long_title():
    """Summarizer occasionally ignores the 2-6 word rule; we trim."""
    from gateway.helpers.base import HelperResult

    class _Summ:
        async def invoke(self, task):
            return HelperResult(
                role="summarizer", model_id="x",
                output={"summary":
                        "One Two Three Four Five Six Seven Eight Nine Ten."},
            )
    vc = _FakeVC()
    await _generate_and_set_thread_title(
        summarizer=_Summ(), vault_client=vc,
        thread_id="t1", messages=[{"role": "user", "content": "hi"}],
    )
    assert len(vc.calls) == 1
    _, title = vc.calls[0]
    # Trimmed to first 6 words, trailing period stripped.
    assert title == "One Two Three Four Five Six"


@pytest.mark.asyncio
async def test_generate_and_set_strips_quotes_from_title():
    from gateway.helpers.base import HelperResult

    class _Summ:
        async def invoke(self, task):
            return HelperResult(role="summarizer", model_id="x",
                                output={"summary": '"Star Citizen Ships"'})
    vc = _FakeVC()
    await _generate_and_set_thread_title(
        summarizer=_Summ(), vault_client=vc,
        thread_id="t1", messages=[{"role": "user", "content": "hi"}],
    )
    assert vc.calls == [("t1", "Star Citizen Ships")]
