"""Regression test for the summarizer-feed bug fixed 2026-04-29.

Before the fix `schedule_summarizer_refresh` only handed the
summarizer the current `(user, assistant)` pair, so `mid_summary`
was overwritten every 5 turns with a recap of one turn — the whole
tiered-memory design was structurally present but functionally
inert. The fix pulls the rolling 20-message window from the
LLMClient instead.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from gateway.hive_turn_helpers import schedule_summarizer_refresh


@dataclass
class _FakeTurn:
    reply: str = "ok"
    blocked: bool = False
    error: str | None = None
    turn_id: str = "tk-1"


class _RecordingSummarizer:
    """Records the `messages` list it was handed."""

    def __init__(self) -> None:
        self.messages_seen: list[list[dict]] = []
        self.invoked = 0

    async def invoke(self, task):
        self.invoked += 1
        self.messages_seen.append(list(task.inputs.get("messages") or []))
        from gateway.helpers.base import HelperResult
        return HelperResult(
            role="summarizer", model_id="x",
            output={"summary": "ok", "open_tasks": [],
                    "decisions": [], "user_facts": []},
        )


class _FakeLLM:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.calls: list[tuple[int, int]] = []

    def recent_messages(self, user_id: int, limit: int = 20) -> list[dict]:
        self.calls.append((user_id, limit))
        return list(self._messages[-limit:])


class _AppState:
    def __init__(self, *, llm, summarizer, memory_store) -> None:
        self.background_tasks = set()
        self.adapters = {"terry": type("A", (), {"_llm": llm})()}
        self.helpers = {"summarizer": summarizer}
        self.memory_store_terry = memory_store


@pytest.mark.asyncio
async def test_summarizer_sees_full_window(tmp_path):
    """The fix: refresh hands the summarizer the rolling 20-message
    window, not the current pair."""
    from gateway.conversation_memory import MemoryStore

    twenty = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"msg-{i}"}
        for i in range(20)
    ]
    llm = _FakeLLM(messages=twenty)
    summarizer = _RecordingSummarizer()
    store = MemoryStore(tmp_path, bot="terry")

    # Drive turn count to the refresh threshold (5).
    for _ in range(5):
        store.increment_turn(7)

    state = _AppState(llm=llm, summarizer=summarizer, memory_store=store)
    schedule_summarizer_refresh(state, _FakeTurn(), user_id=7, text="latest")

    await asyncio.gather(*list(state.background_tasks))

    assert summarizer.invoked == 1
    seen = summarizer.messages_seen[0]
    assert len(seen) == 20
    assert seen[0]["content"] == "msg-0"
    assert seen[-1]["content"] == "msg-19"
    assert llm.calls == [(7, 20)]


@pytest.mark.asyncio
async def test_falls_back_to_pair_when_llm_missing(tmp_path):
    """If the LLM isn't reachable we fall back to the (user, assistant)
    pair so we still produce *something* — better than silently
    failing the refresh."""
    from gateway.conversation_memory import MemoryStore

    summarizer = _RecordingSummarizer()
    store = MemoryStore(tmp_path, bot="terry")
    for _ in range(5):
        store.increment_turn(9)

    class _NoAdapters:
        background_tasks = set()
        adapters: dict = {}
        helpers = {"summarizer": summarizer}

    state = _NoAdapters()
    state.memory_store_terry = store
    schedule_summarizer_refresh(
        state, _FakeTurn(reply="hi back"), user_id=9, text="hi",
    )

    await asyncio.gather(*list(state.background_tasks))

    assert summarizer.invoked == 1
    seen = summarizer.messages_seen[0]
    assert seen == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hi back"},
    ]


@pytest.mark.asyncio
async def test_no_refresh_below_threshold(tmp_path):
    """Sanity: only refresh when the turn-count threshold is hit."""
    from gateway.conversation_memory import MemoryStore

    summarizer = _RecordingSummarizer()
    store = MemoryStore(tmp_path, bot="terry")
    store.increment_turn(1)   # only 1 turn — below the 5-turn threshold
    state = _AppState(llm=_FakeLLM([]), summarizer=summarizer,
                      memory_store=store)
    schedule_summarizer_refresh(state, _FakeTurn(), user_id=1, text="x")

    if state.background_tasks:
        await asyncio.gather(*list(state.background_tasks))

    assert summarizer.invoked == 0
