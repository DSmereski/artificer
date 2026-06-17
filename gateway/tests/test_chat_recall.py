"""Tests for the chat_recall helper — direct FTS5 lookup, no LLM."""

from __future__ import annotations

import pytest

from gateway.helpers.base import HelperTask
from gateway.helpers.chat_recall import ChatRecallHelper


class _FakeVaultClient:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    def search_chat(self, *, bot, user_id, query_text, limit=20, thread_id=None):
        self.calls.append({
            "bot": bot, "user_id": user_id, "query_text": query_text,
            "limit": limit, "thread_id": thread_id,
        })
        return list(self.rows)


def _helper(rows, **overrides):
    factory = lambda: _FakeVaultClient(rows)
    kwargs = dict(
        model_id="planner-qwen", ollama_name="planner-qwen",
        prompt_name="prompts/librarian.md", params={},
        invoker=None, timeout_s=5, schema=None,
        vault_client_factory=factory,
    )
    kwargs.update(overrides)
    return ChatRecallHelper(**kwargs), factory


@pytest.mark.asyncio
async def test_returns_hits_for_matching_query():
    helper, factory = _helper([
        {"role": "user", "content": "what about the kraken?",
         "thread_id": "default", "created_at": 1000},
        {"role": "assistant", "content": "the kraken is a sea monster",
         "thread_id": "default", "created_at": 1001},
    ])
    task = HelperTask(role="chat_recall", goal="recall",
                      inputs={"query": "kraken", "user_id": 42})
    result = await helper.invoke(task)
    assert result.error is None
    hits = result.output["hits"]
    assert len(hits) == 2
    assert hits[0]["content"] == "what about the kraken?"
    assert "2 chat-log hits" in result.output["summary"]
    assert result.confidence == "high"


@pytest.mark.asyncio
async def test_uses_goal_when_no_query_input():
    helper, _ = _helper([
        {"role": "user", "content": "hi", "thread_id": "t",
         "created_at": 1},
    ])
    task = HelperTask(role="chat_recall", goal="penguin",
                      inputs={"user_id": 1})
    result = await helper.invoke(task)
    assert result.output["hits"]


@pytest.mark.asyncio
async def test_passes_bot_and_thread_through():
    helper, _ = _helper([])
    captured: list[dict] = []

    class _Cap:
        def search_chat(self, **kw):
            captured.append(kw)
            return []
    helper._vault_client_factory = lambda: _Cap()

    task = HelperTask(role="chat_recall", goal="x",
                      inputs={"query": "q", "user_id": 5,
                              "bot": "scout", "thread_id": "abc"})
    await helper.invoke(task)
    assert captured[0]["bot"] == "scout"
    assert captured[0]["thread_id"] == "abc"
    assert captured[0]["user_id"] == 5


@pytest.mark.asyncio
async def test_zero_hits_low_confidence():
    helper, _ = _helper([])
    task = HelperTask(role="chat_recall", goal="r",
                      inputs={"query": "missing", "user_id": 1})
    result = await helper.invoke(task)
    assert result.output["hits"] == []
    assert "no chat-log hits" in result.output["summary"]
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_missing_factory_short_circuits():
    helper, _ = _helper([], vault_client_factory=None)
    task = HelperTask(role="chat_recall", goal="r",
                      inputs={"query": "x", "user_id": 1})
    result = await helper.invoke(task)
    assert result.output["hits"] == []
    assert result.output["summary"] == "chat_log not configured (no vault client)"
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_missing_user_id_short_circuits():
    helper, _ = _helper([])
    task = HelperTask(role="chat_recall", goal="r",
                      inputs={"query": "x"})
    result = await helper.invoke(task)
    assert result.output["hits"] == []
    assert "no user_id in inputs" in result.output["summary"]


@pytest.mark.asyncio
async def test_missing_query_short_circuits():
    helper, _ = _helper([])
    task = HelperTask(role="chat_recall", goal="",
                      inputs={"user_id": 1})
    result = await helper.invoke(task)
    assert result.output["hits"] == []
    assert "no query supplied" in result.output["summary"]


@pytest.mark.asyncio
async def test_truncates_long_content():
    long = "x" * 1000
    helper, _ = _helper([
        {"role": "assistant", "content": long, "thread_id": "t",
         "created_at": 1},
    ])
    task = HelperTask(role="chat_recall", goal="r",
                      inputs={"query": "q", "user_id": 1})
    result = await helper.invoke(task)
    body = result.output["hits"][0]["content"]
    assert body.endswith("...")
    assert len(body) <= 700  # 600 cap + ellipsis padding


@pytest.mark.asyncio
async def test_search_exception_returns_error_summary():
    class _Boom:
        def search_chat(self, **kw):
            raise RuntimeError("disk full")

    helper, _ = _helper([])
    helper._vault_client_factory = lambda: _Boom()
    task = HelperTask(role="chat_recall", goal="r",
                      inputs={"query": "q", "user_id": 1})
    result = await helper.invoke(task)
    assert result.output["hits"] == []
    assert "search error" in result.output["summary"]
    assert result.confidence == "low"
