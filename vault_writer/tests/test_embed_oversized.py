"""Tests for oversized-document handling in the embed pipeline.

Covers:
- Chunking splits large content within the configured size limit.
- The daemon marks a source path as skipped after N consecutive
  EmbeddingError failures and stops calling the embedder.
- Pre-skipped paths are never passed to the embedder.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio  # noqa: F401

from vault_writer.config import AuthConfig, Config, GiteaConfig, ScanConfig, SearchConfig
from vault_writer.daemon import Daemon, _EMBED_FAIL_LIMIT
from vault_writer.embedder import EmbeddingError, chunk_text, _CHUNK_SIZE


# ---------------------------------------------------------------------------
# chunk_text unit tests
# ---------------------------------------------------------------------------

def test_oversized_doc_chunked_within_size_limit() -> None:
    """A 100 KB blob must be split into chunks each ≤ _CHUNK_SIZE characters."""
    blob = "x " * 50_000  # 100 000 characters, no paragraph breaks
    chunks = chunk_text(blob)
    assert len(chunks) > 1, "100 KB blob should produce multiple chunks"
    for i, chunk in enumerate(chunks):
        assert len(chunk) <= _CHUNK_SIZE, (
            f"chunk {i} has {len(chunk)} chars, expected ≤ {_CHUNK_SIZE}"
        )


def test_oversized_doc_paragraph_split_preferred() -> None:
    """When paragraph boundaries exist they should be used as split points."""
    paragraph = "Word " * 200 + "\n\n"  # ~1000 chars + blank line
    blob = paragraph * 20  # ~20 000 chars
    chunks = chunk_text(blob)
    # At least one chunk should end with a paragraph break.
    ends_at_para = any(c.endswith("\n\n") for c in chunks)
    assert ends_at_para, "expected at least one chunk to end at a paragraph boundary"


def test_short_text_is_not_chunked() -> None:
    """Text shorter than chunk_size must be returned as a single chunk."""
    short = "Hello, world. " * 10
    chunks = chunk_text(short)
    assert chunks == [short]


def test_empty_text_returns_one_chunk() -> None:
    chunks = chunk_text("")
    assert chunks == [""]


def test_single_massive_line_chunked() -> None:
    """No paragraph boundary — fall back to hard character split."""
    line = "a" * 20_000
    chunks = chunk_text(line)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= _CHUNK_SIZE


# ---------------------------------------------------------------------------
# Daemon integration tests
# ---------------------------------------------------------------------------

DIMENSION = 8


def _make_config(tmp_vault: Path) -> Config:
    return Config(
        vault_path=tmp_vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=0,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=DIMENSION,
        chunk_max_chars=4000,
        gitea=GiteaConfig(
            remote="", token_env="GITEA_TOKEN",
            push_on_write=False, batch_window_seconds=5,
        ),
        search=SearchConfig(default_k=5, min_score=0.4),
        scan=ScanConfig(initial_full_scan=False, periodic_seconds=0,
                        reconcile_orphans=False),
        auth=AuthConfig(token_path=None),
    )


def _note_text(body: str = "hello") -> str:
    return (
        "---\ntype: knowledge\nauthor: terry\naudience: [all]\n"
        f"title: Test\n---\n\n{body}\n"
    )


class _GoodEmbedder:
    """Always succeeds; records call count."""

    dimension = DIMENSION

    def __init__(self) -> None:
        self.call_count = 0

    async def embed(self, text: str, *, kind: str = "document") -> list[float]:
        self.call_count += 1
        return [0.1] * DIMENSION

    async def embed_chunks(
        self, text: str, *, kind: str = "document", chunk_size: int | None = None,
    ) -> list[list[float]]:
        from vault_writer.embedder import chunk_text, _CHUNK_SIZE
        chunks = chunk_text(text, chunk_size=chunk_size or _CHUNK_SIZE)
        result = []
        for chunk in chunks:
            self.call_count += 1
            result.append([0.1] * DIMENSION)
        return result


class _FailEmbedder:
    """Always raises EmbeddingError; records call count."""

    dimension = DIMENSION

    def __init__(self) -> None:
        self.call_count = 0

    async def embed(self, text: str, *, kind: str = "document") -> list[float]:
        self.call_count += 1
        raise EmbeddingError(
            "ollama returned 500: the input length exceeds the context length"
        )

    async def embed_chunks(
        self, text: str, *, kind: str = "document", chunk_size: int | None = None,
    ) -> list[list[float]]:
        self.call_count += 1
        raise EmbeddingError(
            "ollama returned 500: the input length exceeds the context length"
        )


@pytest.mark.asyncio
async def test_oversized_doc_chunked_and_indexed(tmp_path: Path) -> None:
    """A document whose body is 100 KB must be indexed successfully.

    The embedder receives the first chunk (≤ _CHUNK_SIZE chars) rather than
    the raw 100 KB body, so Ollama never sees the oversized payload.
    """
    # Arrange: vault with a massive note.
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    blob = "word " * 20_000  # 100 000 characters
    note_path = vault / "knowledge" / "big.md"
    note_path.write_text(_note_text(blob), encoding="utf-8")

    received_texts: list[str] = []

    class _CapturingEmbedder:
        dimension = DIMENSION

        async def embed(self, text: str, *, kind: str = "document") -> list[float]:
            received_texts.append(text)
            return [0.1] * DIMENSION

        async def embed_chunks(
            self, text: str, *, kind: str = "document", chunk_size: int | None = None,
        ) -> list[list[float]]:
            from vault_writer.embedder import chunk_text, _CHUNK_SIZE
            chunks = chunk_text(text, chunk_size=chunk_size or _CHUNK_SIZE)
            result = []
            for chunk in chunks:
                received_texts.append(chunk)
                result.append([0.1] * DIMENSION)
            return result

    config = Config(
        vault_path=vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=0,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=DIMENSION,
        chunk_max_chars=4000,
        gitea=GiteaConfig(
            remote="", token_env="GITEA_TOKEN",
            push_on_write=False, batch_window_seconds=5,
        ),
        search=SearchConfig(default_k=5, min_score=0.4),
        scan=ScanConfig(initial_full_scan=True, periodic_seconds=0,
                        reconcile_orphans=False),
        auth=AuthConfig(token_path=None),
    )

    daemon = Daemon(config, embedder=_CapturingEmbedder())
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)
        # The note must be indexed.
        assert daemon.index.count() == 1, "oversized note should be indexed"
        # The embedder must have received chunks, never the raw blob.
        # With multi-chunk embedding enabled, there are multiple entries
        # (one per chunk). All of them must be within the size limit.
        assert len(received_texts) >= 1, "embedder should have received at least one chunk"
        for i, chunk_text_received in enumerate(received_texts):
            assert len(chunk_text_received) <= _CHUNK_SIZE, (
                f"chunk {i} has {len(chunk_text_received)} chars, expected ≤ {_CHUNK_SIZE}"
            )
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_chunked_embedding_failure_marked_skipped(tmp_path: Path) -> None:
    """When the embedder always raises EmbeddingError the source path must be
    stamped as skipped after _EMBED_FAIL_LIMIT attempts and the embedder must
    not be called again for that path.

    We drive the failure counter up by enqueuing the file _EMBED_FAIL_LIMIT
    times (simulating repeated watchdog events), then assert the circuit
    breaker fires and blocks subsequent calls.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    note_path = vault / "knowledge" / "imagegen-loras.md"
    note_path.write_text(_note_text("some content"), encoding="utf-8")

    embedder = _FailEmbedder()
    config = Config(
        vault_path=vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=0,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=DIMENSION,
        chunk_max_chars=4000,
        gitea=GiteaConfig(
            remote="", token_env="GITEA_TOKEN",
            push_on_write=False, batch_window_seconds=5,
        ),
        search=SearchConfig(default_k=5, min_score=0.4),
        # No initial scan — we control exactly how many events fire.
        scan=ScanConfig(initial_full_scan=False, periodic_seconds=0,
                        reconcile_orphans=False),
        auth=AuthConfig(token_path=None),
    )

    daemon = Daemon(config, embedder=embedder)
    await daemon.start()
    try:
        rel_path = "knowledge/imagegen-loras.md"

        # Enqueue _EMBED_FAIL_LIMIT upsert events to exhaust the retry budget.
        for _ in range(_EMBED_FAIL_LIMIT):
            await daemon._queue.put(("upsert", note_path))
        await daemon.wait_idle(timeout=5.0)

        # Must be marked as permanently skipped after _EMBED_FAIL_LIMIT tries.
        assert daemon._embed_failures.get(rel_path, 0) >= _EMBED_FAIL_LIMIT, (
            f"expected ≥ {_EMBED_FAIL_LIMIT} failures recorded, "
            f"got {daemon._embed_failures.get(rel_path, 0)}"
        )
        # The note must NOT be in the index (it failed to embed).
        assert daemon.index.count() == 0

        # Now enqueue the same file again — the circuit breaker must prevent
        # any further embedder calls.
        calls_before = embedder.call_count
        await daemon._queue.put(("upsert", note_path))
        await daemon.wait_idle(timeout=5.0)
        assert embedder.call_count == calls_before, (
            "embedder should not be called again after path is skipped"
        )
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_skipped_doc_not_retried(tmp_path: Path) -> None:
    """A path pre-loaded into _embed_failures at the limit must never reach
    the embedder when the daemon processes a upsert event for it.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    note_path = vault / "knowledge" / "already-skipped.md"
    note_path.write_text(_note_text("body"), encoding="utf-8")

    embedder = _FailEmbedder()
    config = Config(
        vault_path=vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=0,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=DIMENSION,
        chunk_max_chars=4000,
        gitea=GiteaConfig(
            remote="", token_env="GITEA_TOKEN",
            push_on_write=False, batch_window_seconds=5,
        ),
        search=SearchConfig(default_k=5, min_score=0.4),
        # No initial scan — we control the queue manually.
        scan=ScanConfig(initial_full_scan=False, periodic_seconds=0,
                        reconcile_orphans=False),
        auth=AuthConfig(token_path=None),
    )

    daemon = Daemon(config, embedder=embedder)
    await daemon.start()
    try:
        # Pre-populate the failure counter as if this path had already hit the
        # limit in a prior scan cycle.
        rel_path = "knowledge/already-skipped.md"
        daemon._embed_failures[rel_path] = _EMBED_FAIL_LIMIT

        # Enqueue an upsert event for the pre-skipped path.
        await daemon._queue.put(("upsert", note_path))
        await daemon.wait_idle(timeout=5.0)

        # The embedder must never have been called.
        assert embedder.call_count == 0, (
            f"embedder called {embedder.call_count} time(s) for a pre-skipped path"
        )
        assert daemon.index.count() == 0
    finally:
        await daemon.stop()
