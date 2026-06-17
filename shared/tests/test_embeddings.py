"""Cache behaviour for shared.embeddings.embed_text.

Doesn't hit Ollama — patches httpx.AsyncClient to a fake that records
calls and returns canned vectors. The point is to assert that repeated
calls for the same (model, text) hit the cache, that failures aren't
cached, and that the LRU bound holds.
"""
from __future__ import annotations

from typing import Any

import pytest

from shared import embeddings


class _FakeResp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient that counts POSTs."""

    instances: list["_FakeClient"] = []

    def __init__(self, *, base_url: str, timeout: float) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.posts: list[dict] = []
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, path: str, *, json: dict) -> _FakeResp:
        self.posts.append({"path": path, "json": json})
        prompt = json.get("prompt", "")
        # Return an embedding whose first value encodes the prompt
        # length so different inputs produce distinguishable vectors.
        return _FakeResp(200, {"embedding": [float(len(prompt)), 0.0, 1.0]})


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    embeddings.reset_embedding_cache()
    _FakeClient.instances.clear()
    monkeypatch.setattr(embeddings.httpx, "AsyncClient", _FakeClient)
    yield
    embeddings.reset_embedding_cache()


@pytest.mark.asyncio
async def test_repeat_call_hits_cache():
    v1 = await embeddings.embed_text(
        "hello world", ollama_url="http://x", model="nomic-embed-text",
    )
    v2 = await embeddings.embed_text(
        "hello world", ollama_url="http://x", model="nomic-embed-text",
    )
    assert v1 == v2
    stats = embeddings.embedding_cache_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    # Only one httpx client / one POST despite two calls.
    assert sum(len(c.posts) for c in _FakeClient.instances) == 1


@pytest.mark.asyncio
async def test_different_text_misses():
    await embeddings.embed_text(
        "a", ollama_url="http://x", model="nomic-embed-text",
    )
    await embeddings.embed_text(
        "b", ollama_url="http://x", model="nomic-embed-text",
    )
    stats = embeddings.embedding_cache_stats()
    assert stats["misses"] == 2
    assert stats["hits"] == 0


@pytest.mark.asyncio
async def test_different_model_distinct_cache_keys():
    await embeddings.embed_text(
        "same text", ollama_url="http://x", model="model-a",
    )
    await embeddings.embed_text(
        "same text", ollama_url="http://x", model="model-b",
    )
    stats = embeddings.embedding_cache_stats()
    assert stats["misses"] == 2


@pytest.mark.asyncio
async def test_empty_text_returns_none_without_calling():
    v = await embeddings.embed_text(
        "", ollama_url="http://x", model="nomic-embed-text",
    )
    assert v is None
    assert _FakeClient.instances == []


@pytest.mark.asyncio
async def test_failure_not_cached(monkeypatch):
    class _FailingClient(_FakeClient):
        async def post(self, path, *, json):
            self.posts.append({"path": path, "json": json})
            return _FakeResp(500, {})

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", _FailingClient)
    v = await embeddings.embed_text(
        "x", ollama_url="http://x", model="m",
    )
    assert v is None
    # Now succeed: must re-hit Ollama (i.e. not cached as None).
    monkeypatch.setattr(embeddings.httpx, "AsyncClient", _FakeClient)
    v2 = await embeddings.embed_text(
        "x", ollama_url="http://x", model="m",
    )
    assert v2 is not None
    stats = embeddings.embedding_cache_stats()
    assert stats["errors"] == 1
    assert stats["misses"] == 2
