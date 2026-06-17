"""Smoke test for the wired-up Researcher helper."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from gateway.helpers.base import HelperTask, OllamaInvoker
from gateway.helpers.researcher import ResearcherHelper
from gateway.helpers.shapes import ResearchPlan
from gateway.safe_fetcher import FetchResult


class _FakeInvoker(OllamaInvoker):
    def __init__(self, responses) -> None:
        super().__init__()
        self._responses = list(responses)

    async def chat(self, *, model, system, user, params=None, use_cpu=False):
        if not self._responses:
            return "{}", 0, 0
        return self._responses.pop(0), 5, 10


@pytest.mark.asyncio
async def test_researcher_helper_returns_facts_when_corroborated(monkeypatch):
    # Mock search -> 2 urls.
    async def fake_search(topic, k=5):
        return ["https://a.example.com", "https://b.example.com"]

    async def fake_fetch(url):
        return FetchResult(
            url_final=url, title=url, text="some body",
            status=200, fetched_at=time.time(),
        )

    monkeypatch.setattr("gateway.helpers.researcher.ddg_search", fake_search)
    monkeypatch.setattr("gateway.helpers.researcher.safe_fetch", fake_fetch)

    invoker = _FakeInvoker([
        # extractor for source 0
        '{"claims": [{"claim": "X is fast", "span": "X is fast"}]}',
        # extractor for source 1
        '{"claims": [{"claim": "X is fast (per b)", "span": "..."}]}',
        # consolidator
        '{"matches": [{"claim": "X is fast", "sources": [0,1]}]}',
    ])
    h = ResearcherHelper(
        model_id="qwen-8b", ollama_name="qwen3:8b",
        prompt_name="researcher", params={}, invoker=invoker,
        timeout_s=60, schema=ResearchPlan,
    )
    result = await h.invoke(HelperTask(
        role="researcher", goal="research X",
        inputs={"topic": "X"},
    ))
    assert result.error is None
    assert len(result.output["facts"]) == 1
    # Deterministic consolidator picks the longest paraphrase as the
    # canonical wording — both 'X is fast' and 'X is fast (per b)'
    # collapse into one fact, with the longer string winning.
    fact_text = result.output["facts"][0]["claim"].lower()
    assert "x is fast" in fact_text
    assert result.confidence == "high"
    assert result.citations == ["https://a.example.com", "https://b.example.com"]
    # Tokens should accumulate from both extractor calls + consolidator.
    assert result.tokens_in > 0


@pytest.mark.asyncio
async def test_researcher_helper_missing_topic():
    h = ResearcherHelper(
        model_id="qwen-8b", ollama_name="qwen3:8b",
        prompt_name="researcher", params={}, invoker=_FakeInvoker([]),
        timeout_s=60, schema=ResearchPlan,
    )
    result = await h.invoke(HelperTask(
        role="researcher", goal="x", inputs={},
    ))
    assert result.error is not None
    assert "topic" in result.error.lower()
