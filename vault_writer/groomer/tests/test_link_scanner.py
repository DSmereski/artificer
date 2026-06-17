# vault_writer/groomer/tests/test_link_scanner.py
"""Tests for link_scanner — broken [[wikilinks]] flagging."""
from __future__ import annotations

import time
from pathlib import Path

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.scanners.link_scanner import scan


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_flags_broken_wikilink(tmp_path: Path) -> None:
    _write(tmp_path / "people" / "penguin.md", "See [[Missing Note]] for...")
    _write(tmp_path / "people" / "exists.md", "ok")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    out = scan(ctx)
    assert len(out) == 1
    assert out[0].kind == "link_scanner"
    assert "Missing Note" in out[0].body_md
    assert "people/penguin.md" in out[0].body_md


def test_resolves_existing_link(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "See [[b]] for context.")
    _write(tmp_path / "b.md", "I exist.")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    assert scan(ctx) == []


def test_ignores_aliases_and_headings(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "See [[exists#Heading|Display]] and [[exists]].")
    _write(tmp_path / "exists.md", "ok")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    assert scan(ctx) == []


def test_skips_ops_dir(tmp_path: Path) -> None:
    _write(tmp_path / "ops" / "audits" / "x.md", "[[Missing]]")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    assert scan(ctx) == []


def test_different_dirs_same_stem_produce_different_slugs(tmp_path: Path) -> None:
    """people/alice.md and projects/alice.md both linking [[Gone]] must not collide."""
    _write(tmp_path / "people" / "alice.md", "See [[Gone]] here.")
    _write(tmp_path / "projects" / "alice.md", "See [[Gone]] here.")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    out = scan(ctx)
    assert len(out) == 2
    slugs = {s.slug for s in out}
    assert len(slugs) == 2, f"Expected 2 distinct slugs, got: {slugs}"
