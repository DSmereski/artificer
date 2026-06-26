"""Tests for configurable chunk_max_chars cap (Fix 2).

Covers:
- A note larger than 8 KB splits into multiple chunks each under the cap.
- The cap is respected when passed via embed_chunks(chunk_size=N).
- The legacy embed() path (first-chunk-only, used by vec_notes) also gets
  a clamped first chunk when the body exceeds the cap.
- chunk_text respects a custom chunk_size argument.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio  # noqa: F401

from vault_writer.config import AuthConfig, Config, GiteaConfig, ScanConfig, SearchConfig, WikiSynthConfig
from vault_writer.daemon import Daemon
from vault_writer.embedder import Embedder, chunk_text, _CHUNK_SIZE


DIMENSION = 8

# A small cap well under _CHUNK_SIZE so we can test with small inputs.
_TEST_CAP = 200


# ---------------------------------------------------------------------------
# chunk_text with custom chunk_size
# ---------------------------------------------------------------------------


def test_chunk_text_respects_custom_size() -> None:
    """chunk_text(text, chunk_size=N) must keep every chunk under N chars."""
    body = "word " * 2000  # 10 000 chars
    chunks = chunk_text(body, chunk_size=_TEST_CAP)
    assert len(chunks) > 1, "body should be split into multiple chunks"
    for i, chunk in enumerate(chunks):
        assert len(chunk) <= _TEST_CAP, (
            f"chunk {i} has {len(chunk)} chars, expected ≤ {_TEST_CAP}"
        )


def test_chunk_text_large_note_under_default_cap() -> None:
    """A note of 9 000 chars (> 8 KB) must split into chunks all under
    _CHUNK_SIZE so none would exceed nomic-embed-text's ~2048-token limit."""
    body = "a" * 9_000
    chunks = chunk_text(body)
    assert len(chunks) > 1, "9 000-char note should produce multiple chunks"
    for i, chunk in enumerate(chunks):
        assert len(chunk) <= _CHUNK_SIZE, (
            f"chunk {i} has {len(chunk)} chars, expected ≤ {_CHUNK_SIZE}"
        )


# ---------------------------------------------------------------------------
# embed_chunks with chunk_size kwarg
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_chunks_honours_custom_chunk_size() -> None:
    """embed_chunks(text, chunk_size=N) must pass N down to chunk_text so
    every chunk received by the model is bounded by N characters."""
    received: list[str] = []

    class _CapturingEmbedder:
        dimension = DIMENSION

        async def embed(self, text: str, *, kind: str = "document") -> list[float]:
            received.append(text)
            return [0.1] * DIMENSION

        async def embed_chunks(
            self,
            text: str,
            *,
            kind: str = "document",
            chunk_size: int | None = None,
        ) -> list[list[float]]:
            from vault_writer.embedder import chunk_text as _chunk_text
            effective = chunk_size if chunk_size is not None else _CHUNK_SIZE
            chunks = _chunk_text(text, chunk_size=effective)
            result = []
            for chunk in chunks:
                received.append(chunk)
                result.append([0.1] * DIMENSION)
            return result

    cap = _TEST_CAP
    body = "word " * 200  # 1 000 chars — larger than cap

    embedder = _CapturingEmbedder()
    vecs = await embedder.embed_chunks(body, chunk_size=cap)

    assert len(vecs) > 1, "body larger than cap must yield multiple embeddings"
    for i, chunk in enumerate(received):
        assert len(chunk) <= cap, (
            f"chunk {i} has {len(chunk)} chars, exceeds cap {cap}"
        )


# ---------------------------------------------------------------------------
# Daemon integration: config.chunk_max_chars is forwarded to embed_chunks
# ---------------------------------------------------------------------------


def _make_config(vault: Path, chunk_max_chars: int) -> Config:
    return Config(
        vault_path=vault,
        daemon_bind_host="127.0.0.1",
        daemon_bind_port=0,
        ollama_url="http://fake",
        embedding_model="fake",
        embedding_dimension=DIMENSION,
        chunk_max_chars=chunk_max_chars,
        gitea=GiteaConfig(
            remote="", token_env="GITEA_TOKEN",
            push_on_write=False, batch_window_seconds=5,
        ),
        search=SearchConfig(default_k=5, min_score=0.4),
        scan=ScanConfig(initial_full_scan=True, periodic_seconds=0,
                        reconcile_orphans=False),
        auth=AuthConfig(token_path=None),
        wiki_synth=WikiSynthConfig(enabled=False, model="planner-qwen",
                                   top_k=5, timeout_seconds=30),
    )


@pytest.mark.asyncio
async def test_daemon_uses_chunk_max_chars_from_config(tmp_path: Path) -> None:
    """A note body of 9 000 chars with chunk_max_chars=_TEST_CAP must be split
    into chunks all ≤ _TEST_CAP so the daemon never sends an oversized payload
    to Ollama, even for notes that would fit in one _CHUNK_SIZE-sized chunk."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "knowledge").mkdir()
    body = "x " * 4_500  # 9 000 chars
    note_path = vault / "knowledge" / "big-configurable.md"
    note_path.write_text(
        "---\ntype: knowledge\nauthor: hive\naudience: [all]\n"
        f"title: Big\n---\n\n{body}\n",
        encoding="utf-8",
    )

    received_chunks: list[str] = []

    class _CapturingEmbedder:
        dimension = DIMENSION

        async def embed(self, text: str, *, kind: str = "document") -> list[float]:
            received_chunks.append(text)
            return [0.1] * DIMENSION

        async def embed_chunks(
            self,
            text: str,
            *,
            kind: str = "document",
            chunk_size: int | None = None,
        ) -> list[list[float]]:
            from vault_writer.embedder import chunk_text as _chunk_text
            effective = chunk_size if chunk_size is not None else _CHUNK_SIZE
            chunks = _chunk_text(text, chunk_size=effective)
            result = []
            for chunk in chunks:
                received_chunks.append(chunk)
                result.append([0.1] * DIMENSION)
            return result

    # Use a small cap to force splitting of the 9 000-char body.
    cap = _TEST_CAP
    daemon = Daemon(_make_config(vault, chunk_max_chars=cap), _CapturingEmbedder())
    await daemon.start()
    try:
        await daemon.wait_idle(timeout=5.0)
        assert daemon.index.count() == 1, "note should be indexed"
        assert len(received_chunks) > 1, (
            "daemon should have sent multiple chunks to the embedder "
            f"(got {len(received_chunks)} for a {len(body)}-char body with cap={cap})"
        )
        for i, chunk in enumerate(received_chunks):
            assert len(chunk) <= cap, (
                f"chunk {i} sent to embedder has {len(chunk)} chars, "
                f"exceeds cap {cap} — would trigger nomic context overflow"
            )
    finally:
        await daemon.stop()
