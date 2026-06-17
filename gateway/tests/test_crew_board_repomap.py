"""Unit tests for the repo-map symbol index (P7)."""

from __future__ import annotations

from pathlib import Path

from gateway.crew_board.repomap import build_repo_map, find_symbol


def _seed(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / "core.py").write_text(
        "import os\n\n"
        "def helper(a, b=1, *args, **kw):\n"
        "    return a + b\n\n"
        "class Engine(Base):\n"
        "    def start(self, speed):\n"
        "        return speed\n"
        "    async def stop(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    (root / "pkg" / "broken.py").write_text("def (:\n", encoding="utf-8")


def test_build_repo_map_lists_signatures(tmp_path: Path) -> None:
    _seed(tmp_path)
    m = build_repo_map(tmp_path)
    assert "pkg/core.py" in m
    assert "def helper(a, b=…, *args, **kw)" in m
    assert "class Engine(Base)" in m
    assert "def start(self, speed)" in m
    assert "async def stop(self)" in m
    # Broken file is skipped, not crashed on.
    assert "broken.py" not in m


def test_build_repo_map_respects_budget(tmp_path: Path) -> None:
    _seed(tmp_path)
    tiny = build_repo_map(tmp_path, token_budget=1)
    # First file's block still emitted (we never drop the first), but no
    # runaway — output stays small.
    assert len(tiny) < 2000


def test_find_symbol_exact(tmp_path: Path) -> None:
    _seed(tmp_path)
    hits = find_symbol(tmp_path, "Engine")
    assert hits and hits[0]["path"] == "pkg/core.py"
    assert hits[0]["signature"].startswith("class Engine")
    assert hits[0]["line"] == 6


def test_find_symbol_substring(tmp_path: Path) -> None:
    _seed(tmp_path)
    hits = find_symbol(tmp_path, "help")
    assert any(h["signature"].startswith("def helper") for h in hits)


def test_find_symbol_no_match(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert find_symbol(tmp_path, "Nonexistent") == []


def test_symbol_cache_reuses_unchanged_file(tmp_path, monkeypatch):
    from gateway.crew_board import repomap
    _seed(tmp_path)
    # First build populates the cache; second build with the file
    # unchanged must NOT re-parse (ast.parse not called again).
    repomap.build_repo_map(tmp_path)
    import ast as _ast
    calls = {"n": 0}
    real = _ast.parse
    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(repomap.ast, "parse", counting)
    repomap.build_repo_map(tmp_path)
    assert calls["n"] == 0  # served entirely from the mtime cache
