# vault_writer/groomer/tests/test_dup_scanner.py
"""Tests for dup_scanner — pairwise cosine similarity over note embeddings."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.scanners.dup_scanner import scan


class _FakeIndex:
    """Tiny stub that returns a fixed list of (path, embedding) rows."""

    def __init__(self, rows: list[tuple[str, list[float]]]) -> None:
        self._rows = rows

    def list_note_embeddings(self) -> list[tuple[str, list[float]]]:
        return list(self._rows)


def test_emits_suggestion_for_near_duplicate_pair(tmp_path: Path) -> None:
    # Two embeddings with cosine ~ 0.99.
    a = [1.0, 0.0, 0.0]
    b = [0.99, 0.05, 0.0]
    idx = _FakeIndex([("people/penguin.md", a), ("people/penguin-old.md", b)])
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time(), vault_index=idx)
    out = scan(ctx)
    assert len(out) == 1
    s = out[0]
    assert s.kind == "dup_scanner"
    assert "penguin" in s.body_md
    assert s.confidence > 0.92


def test_skips_below_threshold(tmp_path: Path) -> None:
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]      # orthogonal
    idx = _FakeIndex([("a.md", a), ("b.md", b)])
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time(), vault_index=idx)
    assert scan(ctx) == []


def test_no_index_returns_empty(tmp_path: Path) -> None:
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time(), vault_index=None)
    assert scan(ctx) == []


def test_caps_at_max_suggestions(tmp_path: Path) -> None:
    # 40 near-identical notes → C(40,2) = 780 pairs, capped at 500.
    rows = [(f"n{i}.md", [1.0, 0.001 * i, 0.0]) for i in range(40)]
    idx = _FakeIndex(rows)
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time(), vault_index=idx)
    out = scan(ctx)
    from vault_writer.groomer.suggestion import MAX_SUGGESTIONS_PER_SCAN
    assert len(out) <= MAX_SUGGESTIONS_PER_SCAN
