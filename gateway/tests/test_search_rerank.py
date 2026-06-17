"""Tests for gateway.search_rerank and the LLM re-rank integration.

Unit tests cover:
- Correct re-ordering when the LLM returns a valid index array.
- Failure-tolerance: LLM raises, returns garbage JSON, or invalid indices.
- Candidate cap: only the first 20 candidates are passed to the LLM.
- Feature flag: when flag is off the LLM is never called.

Integration test (one):
- GET /v1/chat/{bot}/search with the flag ON calls the LLM.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidates(n: int, key: str = "content") -> list[dict[str, Any]]:
    """Build n trivial candidate dicts."""
    return [{key: f"snippet {i}", "id": i} for i in range(n)]


# ---------------------------------------------------------------------------
# Unit tests: gateway.search_rerank.llm_rerank
# ---------------------------------------------------------------------------

def test_rerank_reorders_by_llm_score():
    """LLM returns [2, 0, 1] -> candidates come back in that order."""
    from gateway.search_rerank import llm_rerank

    candidates = _make_candidates(3)

    async def fake_chat(*, model, system, user, params, use_cpu):
        return "[2, 0, 1]", 10, 5

    result = asyncio.run(
        llm_rerank("test query", candidates, limit=20, llm_chat=fake_chat)
    )
    assert [c["id"] for c in result] == [2, 0, 1]


def test_rerank_handles_llm_failure():
    """LLM raises -> original RRF order is preserved (failure-tolerant)."""
    from gateway.search_rerank import llm_rerank

    candidates = _make_candidates(3)

    async def failing_chat(*, model, system, user, params, use_cpu):
        raise RuntimeError("ollama down")

    result = asyncio.run(
        llm_rerank("test query", candidates, limit=20, llm_chat=failing_chat)
    )
    assert [c["id"] for c in result] == [0, 1, 2]


def test_rerank_handles_invalid_json():
    """LLM returns non-JSON garbage -> original order preserved."""
    from gateway.search_rerank import llm_rerank

    candidates = _make_candidates(3)

    async def garbage_chat(*, model, system, user, params, use_cpu):
        return "Sorry, I cannot help with that!", 5, 3

    result = asyncio.run(
        llm_rerank("test query", candidates, limit=20, llm_chat=garbage_chat)
    )
    assert [c["id"] for c in result] == [0, 1, 2]


def test_rerank_handles_partial_index_array():
    """LLM returns only a subset of indices -> mentioned ones first,
    then the rest appended in original order."""
    from gateway.search_rerank import llm_rerank

    candidates = _make_candidates(4)

    async def partial_chat(*, model, system, user, params, use_cpu):
        # Only mentions indices 3 and 1; 0 and 2 should be appended.
        return "[3, 1]", 10, 5

    result = asyncio.run(
        llm_rerank("test query", candidates, limit=20, llm_chat=partial_chat)
    )
    ids = [c["id"] for c in result]
    # 3 and 1 first (LLM order), then 0 and 2 (original order).
    assert ids[:2] == [3, 1]
    assert set(ids) == {0, 1, 2, 3}


def test_rerank_caps_candidates():
    """Pass 30 candidates; assert LLM was called with at most 20."""
    from gateway.search_rerank import llm_rerank

    candidates = _make_candidates(30)
    seen_user_msg: list[str] = []

    async def spy_chat(*, model, system, user, params, use_cpu):
        seen_user_msg.append(user)
        return json.dumps(list(range(20))), 10, 5

    result = asyncio.run(
        llm_rerank("test query", candidates, limit=30, llm_chat=spy_chat)
    )
    assert len(seen_user_msg) == 1
    user_msg = seen_user_msg[0]
    # Index 19 should appear in the numbered list; index 20 should not.
    assert "19." in user_msg
    assert "20." not in user_msg
    # All 30 candidates returned (20 re-ranked + 10 tail).
    assert len(result) == 30


def test_rerank_empty_candidates():
    """Empty candidate list -> empty result, LLM never called."""
    from gateway.search_rerank import llm_rerank

    called: list[bool] = []

    async def spy_chat(*, model, system, user, params, use_cpu):
        called.append(True)
        return "[]", 0, 0

    result = asyncio.run(
        llm_rerank("test query", [], limit=20, llm_chat=spy_chat)
    )
    assert result == []
    assert called == []


def test_rerank_limit_applied():
    """limit= is honoured on the returned list."""
    from gateway.search_rerank import llm_rerank

    candidates = _make_candidates(10)

    async def identity_chat(*, model, system, user, params, use_cpu):
        return json.dumps(list(range(10))), 10, 5

    result = asyncio.run(
        llm_rerank("test query", candidates, limit=5, llm_chat=identity_chat)
    )
    assert len(result) == 5


# ---------------------------------------------------------------------------
# Integration test: feature flag gate in the route
# ---------------------------------------------------------------------------

def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_rerank_disabled_skips_llm(client: TestClient, paired_token):
    """Flag OFF (default) -> LLM is never invoked when search route is hit."""
    _, token = paired_token

    with patch("gateway.search_rerank.llm_rerank", new_callable=AsyncMock) as mock_rr:
        r = client.get(
            "/v1/chat/terry/search?q=anything", headers=_auth(token),
        )
    assert r.status_code == 200
    mock_rr.assert_not_called()


def test_rerank_enabled_calls_llm_on_chat_search(tmp_config):
    """Flag ON -> llm_rerank is invoked when /v1/chat/{bot}/search is hit."""
    from gateway.app import create_app
    from gateway.deps import AppState

    cfg_with_flag = replace(tmp_config, feature_search_llm_rerank=True)
    app = create_app(cfg_with_flag)
    prev = app.state.ai_team

    class _FakeVC:
        def search_chat(self, *, bot, user_id, query_text, limit, thread_id=None):
            return [
                {
                    "content": "hello world", "id": 1, "role": "user",
                    "thread_id": "default", "turn_id": None,
                    "bot": bot, "user_id": user_id,
                    "pinned": False, "parent_id": None, "created_at": 0,
                }
            ]

    class _FakeAdapter:
        async def reply_stream(self, user_id, text, *, extra_system=""):
            yield "hi"

        async def reply(self, user_id, text, *, extra_system=""):
            return "hi"

        def reset_history(self, user_id):
            pass

        def status(self):
            return "online"

    app.state.ai_team = AppState(
        config=cfg_with_flag,
        devices=prev.devices,
        pairing=prev.pairing,
        adapters={"terry": _FakeAdapter()},
        scout_history=prev.scout_history,
        image_shim=None,
        event_bus=None,
        ntfy=None,
        vault_client=_FakeVC(),
    )

    new_client = TestClient(app)
    r = new_client.get("/v1/pair/new")
    assert r.status_code == 200
    code = r.json()["code"]
    r = new_client.post(
        "/v1/pair",
        json={"code": code, "name": "test-device", "platform": "test"},
    )
    assert r.status_code == 200
    new_token = r.json()["token"]

    rerank_called: list[bool] = []

    async def fake_rerank(query, candidates, *, limit, llm_chat=None, ollama_name="llama3.2"):
        rerank_called.append(True)
        return candidates[:limit]

    # Patch at the module level so the route's `from gateway.search_rerank
    # import llm_rerank` picks up our fake.
    with patch("gateway.search_rerank.llm_rerank", new=fake_rerank):
        r = new_client.get(
            "/v1/chat/terry/search?q=hello",
            headers=_auth(new_token),
        )

    assert r.status_code == 200
    assert rerank_called, "llm_rerank should have been called with flag ON"


def test_chat_recall_bypasses_rerank():
    """The ChatRecallHelper calls VaultClient.search_chat directly and
    must never invoke llm_rerank, regardless of the feature flag."""
    from gateway.helpers.chat_recall import ChatRecallHelper
    from gateway.helpers.base import HelperTask

    called_search_chat: list[bool] = []
    called_rerank: list[bool] = []

    class _SpyVC:
        def search_chat(self, *, bot, user_id, query_text, limit, thread_id=None):
            called_search_chat.append(True)
            return []

    async def spy_rerank(query, candidates, **kwargs):
        called_rerank.append(True)
        return candidates

    helper = ChatRecallHelper(
        model_id="fake",
        ollama_name="fake",
        prompt_name="planner",
        params={},
        vault_client_factory=lambda: _SpyVC(),
    )

    task = HelperTask(
        role="chat_recall",
        goal="what did we say about kraken?",
        inputs={"query": "kraken", "user_id": 42, "bot": "terry"},
    )

    with patch("gateway.search_rerank.llm_rerank", new=spy_rerank):
        result = asyncio.run(helper.invoke(task))

    assert called_search_chat, "chat_recall must call VaultClient.search_chat"
    assert not called_rerank, "chat_recall must NOT invoke llm_rerank"
