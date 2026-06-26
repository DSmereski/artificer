"""Tests for LibrarianHelper — focus on hybrid-search correctness (#529)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.helpers.librarian import LibrarianHelper


_HELPER_BASE_KWARGS = {
    "model_id": "test-model",
    "ollama_name": "test-model",
    "prompt_name": "librarian",
    "params": {},
}


def _make_fake_vault_client(search_results=None):
    """Return a factory that yields a mock VaultClient."""
    client = MagicMock()
    client.search.return_value = search_results or []

    def factory():
        return client

    return factory, client


@pytest.mark.asyncio
async def test_search_vault_passes_query_text():
    """_search_vault must pass query_text= to client.search so the BM25
    leg of hybrid search is enabled (regression for #529)."""
    factory, mock_client = _make_fake_vault_client()

    helper = LibrarianHelper(
        vault_client_factory=factory,
        ollama_url="http://localhost:11434",
        **_HELPER_BASE_KWARGS,
    )

    fake_embedding = [0.1] * 768

    with patch.object(helper, "_embed", AsyncMock(return_value=fake_embedding)):
        await helper._search_vault("what is penguin's favorite color?", "hive")

    mock_client.search.assert_called_once()
    call_kwargs = mock_client.search.call_args.kwargs

    assert "query_text" in call_kwargs, (
        "query_text= missing from client.search call — BM25 leg is disabled"
    )
    assert call_kwargs["query_text"] == "what is penguin's favorite color?"


@pytest.mark.asyncio
async def test_search_vault_passes_embedding_and_k():
    """Sanity-check that query_embedding and k are also forwarded."""
    factory, mock_client = _make_fake_vault_client()
    helper = LibrarianHelper(
        vault_client_factory=factory,
        ollama_url="http://localhost:11434",
        **_HELPER_BASE_KWARGS,
    )
    fake_embedding = [0.5] * 768

    with patch.object(helper, "_embed", AsyncMock(return_value=fake_embedding)):
        await helper._search_vault("test query", "claude-code")

    kwargs = mock_client.search.call_args.kwargs
    assert kwargs["query_embedding"] == fake_embedding
    assert "k" in kwargs
    assert kwargs["audience"] == "claude-code"


@pytest.mark.asyncio
async def test_search_vault_skipped_when_embed_empty():
    """If embedding returns [], _search_vault returns [] without calling search."""
    factory, mock_client = _make_fake_vault_client()
    helper = LibrarianHelper(
        vault_client_factory=factory,
        ollama_url="http://localhost:11434",
        **_HELPER_BASE_KWARGS,
    )

    with patch.object(helper, "_embed", AsyncMock(return_value=[])):
        result = await helper._search_vault("anything", "hive")

    assert result == []
    mock_client.search.assert_not_called()
