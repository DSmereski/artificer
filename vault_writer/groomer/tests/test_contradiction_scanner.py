# vault_writer/groomer/tests/test_contradiction_scanner.py
"""Unit tests for contradiction_scanner — entity_page truth-vs-timeline drift."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.scanners.contradiction_scanner import scan


class _FakeIndex:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def list_entity_pages_for_contradiction_scan(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeEmbedder:
    """Returns the vector tagged in the row's `_vec_for_<text>` field
    so each test can drive cosine deterministically."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    async def embed(self, text: str) -> list[float]:
        return self._mapping[text]


@pytest.mark.asyncio
async def test_flags_divergence(tmp_path: Path) -> None:
    rows = [{
        "id": "penguin",
        "title": "Penguin",
        "compiled_truth": "alpha truth",
        "recent_timeline_entry": "beta entry",
    }]
    embedder = _FakeEmbedder({
        "alpha truth": [1.0, 0.0, 0.0],
        "beta entry": [0.0, 1.0, 0.0],  # cosine = 0
    })
    ctx = ScanContext(
        vault_path=tmp_path, now_ts=time.time(),
        vault_index=_FakeIndex(rows), embedder=embedder,
    )
    out = await scan(ctx)
    assert len(out) == 1
    assert out[0].kind == "contradiction_scanner"
    assert "penguin" in out[0].slug.lower()


@pytest.mark.asyncio
async def test_skips_aligned_entity(tmp_path: Path) -> None:
    rows = [{
        "id": "x", "title": "X",
        "compiled_truth": "stable truth",
        "recent_timeline_entry": "stable entry",
    }]
    embedder = _FakeEmbedder({
        "stable truth": [1.0, 0.0, 0.0],
        "stable entry": [0.99, 0.05, 0.0],  # cosine ~= 1
    })
    ctx = ScanContext(
        vault_path=tmp_path, now_ts=time.time(),
        vault_index=_FakeIndex(rows), embedder=embedder,
    )
    assert await scan(ctx) == []


@pytest.mark.asyncio
async def test_no_index_returns_empty(tmp_path: Path) -> None:
    ctx = ScanContext(
        vault_path=tmp_path, now_ts=time.time(),
        vault_index=None, embedder=_FakeEmbedder({}),
    )
    assert await scan(ctx) == []


@pytest.mark.asyncio
async def test_no_embedder_returns_empty(tmp_path: Path) -> None:
    rows = [{
        "id": "x", "title": "X",
        "compiled_truth": "a", "recent_timeline_entry": "b",
    }]
    ctx = ScanContext(
        vault_path=tmp_path, now_ts=time.time(),
        vault_index=_FakeIndex(rows), embedder=None,
    )
    assert await scan(ctx) == []


@pytest.mark.asyncio
async def test_missing_method_returns_empty(tmp_path: Path) -> None:
    class _Empty: ...
    ctx = ScanContext(
        vault_path=tmp_path, now_ts=time.time(),
        vault_index=_Empty(), embedder=_FakeEmbedder({}),
    )
    assert await scan(ctx) == []
