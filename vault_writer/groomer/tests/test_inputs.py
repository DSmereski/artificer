# vault_writer/groomer/tests/test_inputs.py
"""Tests for vault note iteration + frontmatter splitting."""
from __future__ import annotations

from pathlib import Path

import pytest

from vault_writer.groomer.inputs import (
    NoteRecord,
    iter_vault_notes,
    split_frontmatter,
)


def test_iter_skips_dotdirs_and_ops(tmp_path: Path) -> None:
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "penguin.md").write_text("# P", encoding="utf-8")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "config.md").write_text("ignore", encoding="utf-8")
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "audits").mkdir()
    (tmp_path / "ops" / "audits" / "x.md").write_text("audit", encoding="utf-8")

    notes = list(iter_vault_notes(tmp_path))
    rels = sorted(n.rel_path for n in notes)
    assert rels == ["people/penguin.md"]


def test_iter_returns_notes_with_mtime(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_text("body", encoding="utf-8")
    [note] = list(iter_vault_notes(tmp_path))
    assert isinstance(note, NoteRecord)
    assert note.rel_path == "n.md"
    assert note.body == "body"
    assert note.mtime > 0
    assert note.frontmatter == {}


def test_split_frontmatter_with_yaml(tmp_path: Path) -> None:
    text = "---\ntitle: T\ntags: [a, b]\n---\nbody here\n"
    fm, body = split_frontmatter(text)
    assert fm == {"title": "T", "tags": ["a", "b"]}
    assert body == "body here\n"


def test_split_frontmatter_no_yaml() -> None:
    fm, body = split_frontmatter("just body")
    assert fm == {}
    assert body == "just body"


def test_split_frontmatter_malformed_returns_empty() -> None:
    # Unterminated frontmatter — body is everything, fm is empty.
    fm, body = split_frontmatter("---\ntitle: T\nno close\n")
    assert fm == {}
    assert body == "---\ntitle: T\nno close\n"


def test_scan_context_notes_walks_vault_once(tmp_path: Path) -> None:
    """ctx.notes() must memoise — repeat callers share the same walk so
    N scanners don't trigger N rglob+frontmatter passes on a 1000-note vault."""
    from vault_writer.groomer.scanners import ScanContext

    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B", encoding="utf-8")
    ctx = ScanContext(vault_path=tmp_path, now_ts=0.0)
    first = ctx.notes()
    # Add a third note AFTER the first call — if we re-walked, this
    # would appear; with caching, it MUST NOT.
    (tmp_path / "c.md").write_text("# C", encoding="utf-8")
    second = ctx.notes()
    assert first is second
    assert len(second) == 2


def test_iter_skips_oversized_notes(tmp_path: Path, monkeypatch) -> None:
    """A markdown file larger than MAX_NOTE_FILE_BYTES must be skipped so
    the groomer can't OOM itself or stall on a stray multi-MB log dump.

    Patch the cap down to a small number rather than writing a real 5 MiB
    file — same code path, faster test."""
    from vault_writer.groomer import inputs as inputs_mod
    monkeypatch.setattr(inputs_mod, "MAX_NOTE_FILE_BYTES", 100)
    (tmp_path / "small.md").write_text("hi", encoding="utf-8")
    (tmp_path / "huge.md").write_text("x" * 500, encoding="utf-8")

    rels = sorted(n.rel_path for n in iter_vault_notes(tmp_path))
    assert rels == ["small.md"]
