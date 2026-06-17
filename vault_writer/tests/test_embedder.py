"""Tests for vault_writer.embedder."""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio  # noqa: F401  (registers the plugin)

from vault_writer.embedder import (
    Embedder,
    EmbeddingError,
    _PREFIX_DOCUMENT,
    _PREFIX_QUERY,
)


@pytest.mark.asyncio
async def test_embed_document_uses_document_prefix() -> None:
    """embed() with kind='document' (default) sends search_document: prefix."""
    received: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.read().decode("utf-8"))
        received.append(body["prompt"])
        return httpx.Response(200, json={"embedding": [0.1] * 768})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        embedder = Embedder(client=client, model="nomic-embed-text", dimension=768)
        await embedder.embed("hello")
        assert received[0] == _PREFIX_DOCUMENT + "hello"


@pytest.mark.asyncio
async def test_embed_query_uses_query_prefix() -> None:
    """embed() with kind='query' sends search_query: prefix."""
    received: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.read().decode("utf-8"))
        received.append(body["prompt"])
        return httpx.Response(200, json={"embedding": [0.1] * 768})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        embedder = Embedder(client=client, model="nomic-embed-text", dimension=768)
        await embedder.embed("what is the kraken", kind="query")
        assert received[0] == _PREFIX_QUERY + "what is the kraken"


@pytest.mark.asyncio
async def test_embed_chunks_propagates_kind() -> None:
    """embed_chunks() with kind='document' prefixes every chunk."""
    received: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.read().decode("utf-8"))
        received.append(body["prompt"])
        return httpx.Response(200, json={"embedding": [0.1] * 768})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        embedder = Embedder(client=client, model="nomic-embed-text", dimension=768)
        from vault_writer.embedder import _CHUNK_SIZE, _CHUNK_OVERLAP
        # A text that produces exactly two chunks:
        # chunk_text advances by (chunk_size - overlap) per step, so we need
        # just over chunk_size characters to get a second chunk.
        text = "Word " * ((_CHUNK_SIZE - _CHUNK_OVERLAP) // 5 + 20)
        vecs = await embedder.embed_chunks(text, kind="document")
        assert len(vecs) >= 1
        for prompt in received:
            assert prompt.startswith(_PREFIX_DOCUMENT)


@pytest.mark.asyncio
async def test_embed_returns_float_list_of_expected_dim() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embeddings"
        import json as _json
        body = _json.loads(request.read().decode("utf-8"))
        # num_gpu=0 forces CPU execution so the 137M F16 embedder never
        # competes with planner-qwen for GPU VRAM. Once Ollama places a
        # model on CPU it stays there until evicted, so any GPU
        # competition for nomic-embed risks pushing planner-qwen off GPU
        # mid-conversation (observed scenario 10, 2026-05-02).
        assert body["options"] == {"num_gpu": 0}
        assert body["model"] == "nomic-embed-text"
        # Prefix is prepended; prompt must start with it.
        assert body["prompt"].startswith(_PREFIX_DOCUMENT)
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3] * 256})  # 768

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        embedder = Embedder(client=client, model="nomic-embed-text", dimension=768)
        vec = await embedder.embed("hello")
        assert len(vec) == 768
        assert all(isinstance(x, float) for x in vec)


@pytest.mark.asyncio
async def test_embed_wrong_dimension_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embedding": [0.0] * 10})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        embedder = Embedder(client=client, model="nomic-embed-text", dimension=768)
        with pytest.raises(EmbeddingError, match="dimension"):
            await embedder.embed("hello")


@pytest.mark.asyncio
async def test_embed_non_200_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
        embedder = Embedder(client=client, model="nomic-embed-text", dimension=768)
        with pytest.raises(EmbeddingError, match="500"):
            await embedder.embed("hello")
