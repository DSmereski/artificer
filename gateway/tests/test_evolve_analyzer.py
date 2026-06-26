"""EV1 — evolution analyzer tests (offline; the LLM synthesis is mocked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gateway.crew_board.store import CrewBoardStore, Project
from gateway.crew_board.evolve import (
    analyze_next, _todo_scan, _pending_signal,
)


class _FakeInvoker:
    """Stands in for OllamaInvoker — returns a canned JSON payload from chat()."""
    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, **_kw):
        return json.dumps(self._payload), 10, 10


class _BoomInvoker:
    async def chat(self, **_kw):
        raise RuntimeError("ollama down")


def _store_with_project(tmp_path: Path) -> CrewBoardStore:
    store = CrewBoardStore(tmp_path / "evolve.db")
    proj = tmp_path / "proj"
    proj.mkdir()
    store.upsert_project(Project(
        slug="proj", path=str(proj), name="Proj",
        enabled=True, push_allowed=False, test_cmd=None))
    return store


def test_analyze_next_ranks_by_score(tmp_path: Path) -> None:
    store = _store_with_project(tmp_path)
    payload = {"candidates": [
        {"title": "Low value", "body": "b", "rationale": "r",
         "source": ["product-idea"], "score": 0.3, "checklist": ["x"]},
        {"title": "High value", "body": "b", "rationale": "r",
         "source": ["repo-gap", "pending"], "score": 0.9, "checklist": ["a", "b"]},
    ]}
    cands = asyncio.run(analyze_next(store, "proj", invoker=_FakeInvoker(payload)))
    assert [c.title for c in cands] == ["High value", "Low value"]
    assert cands[0].source == ["repo-gap", "pending"]
    assert cands[0].score == 0.9
    assert cands[0].checklist == ["a", "b"]


def test_analyze_next_unknown_project_returns_empty(tmp_path: Path) -> None:
    store = CrewBoardStore(tmp_path / "evolve.db")
    out = asyncio.run(analyze_next(store, "nope", invoker=_FakeInvoker({"candidates": []})))
    assert out == []


def test_analyze_next_llm_failure_returns_empty(tmp_path: Path) -> None:
    store = _store_with_project(tmp_path)
    out = asyncio.run(analyze_next(store, "proj", invoker=_BoomInvoker()))
    assert out == []


def test_analyze_next_sanitizes_bad_source_and_score(tmp_path: Path) -> None:
    store = _store_with_project(tmp_path)
    payload = {"candidates": [
        {"title": "Thing", "body": "b", "rationale": "r",
         "source": ["garbage"], "score": "not-a-number", "checklist": []},
    ]}
    cands = asyncio.run(analyze_next(store, "proj", invoker=_FakeInvoker(payload)))
    assert len(cands) == 1
    assert cands[0].source == ["product-idea"]   # bad source → fallback
    assert cands[0].score == 0.0                  # bad score → 0.0


def test_analyze_next_drops_titleless_candidates(tmp_path: Path) -> None:
    store = _store_with_project(tmp_path)
    payload = {"candidates": [
        {"title": "", "body": "b", "rationale": "r", "source": ["repo-gap"],
         "score": 0.5, "checklist": []},
        {"title": "Real", "body": "b", "rationale": "r", "source": ["repo-gap"],
         "score": 0.5, "checklist": []},
    ]}
    cands = asyncio.run(analyze_next(store, "proj", invoker=_FakeInvoker(payload)))
    assert [c.title for c in cands] == ["Real"]


def test_todo_scan_finds_markers(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.py").write_text("x = 1  # TODO fix this later\n", encoding="utf-8")
    (root / "b.ts").write_text("// FIXME broken\nconst y = 2;\n", encoding="utf-8")
    hits = _todo_scan(root)
    assert any("TODO fix this" in h for h in hits)
    assert any("FIXME broken" in h for h in hits)


def test_pending_signal_reads_unchecked_only(tmp_path: Path) -> None:
    root = tmp_path / "r"
    root.mkdir()
    (root / "Pending.md").write_text(
        "- [ ] do the thing\n- [x] already done\n- [ ] another task\n",
        encoding="utf-8")
    text = _pending_signal("noslug", root)
    assert "do the thing" in text
    assert "another task" in text
    assert "already done" not in text


def test_analyze_next_accepts_competitive_source(tmp_path: Path) -> None:
    store = _store_with_project(tmp_path)
    payload = {"candidates": [
        {"title": "Add online multiplayer", "body": "rivals have it",
         "rationale": "table stakes", "source": ["competitive"],
         "score": 0.8, "checklist": ["lobby exists"]},
    ]}
    cands = asyncio.run(analyze_next(store, "proj", invoker=_FakeInvoker(payload)))
    assert cands[0].source == ["competitive"]
