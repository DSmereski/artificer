# vault_writer/groomer/tests/test_format_scanner.py
"""Tests for format_scanner — heading + frontmatter sanity checks."""
from __future__ import annotations

import time
from pathlib import Path

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.scanners.format_scanner import scan


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_flags_h1_missing(tmp_path: Path) -> None:
    _write(tmp_path / "n.md", "## Subhead\n\nbody\n")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    out = scan(ctx)
    assert any("missing h1" in s.body_md.lower() for s in out)


def test_flags_skipped_heading_level(tmp_path: Path) -> None:
    _write(tmp_path / "n.md", "# Title\n\n### Skipped h2\n")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    out = scan(ctx)
    assert any("skipped" in s.body_md.lower() for s in out)


def test_clean_note_no_findings(tmp_path: Path) -> None:
    _write(tmp_path / "n.md", "# T\n\n## S\n\nbody\n")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    assert scan(ctx) == []


def test_unique_slug_per_note(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "## h2\n")
    _write(tmp_path / "b.md", "## h2\n")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    out = scan(ctx)
    slugs = {s.slug for s in out}
    assert len(slugs) == len(out)  # no collisions


def test_different_dirs_same_stem_produce_different_slugs(tmp_path: Path) -> None:
    """people/alice.md and projects/alice.md must not collide on slug 'alice'."""
    _write(tmp_path / "people" / "alice.md", "## h2\n")
    _write(tmp_path / "projects" / "alice.md", "## h2\n")
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    out = scan(ctx)
    assert len(out) == 2
    slugs = {s.slug for s in out}
    assert len(slugs) == 2, f"Expected 2 distinct slugs, got: {slugs}"
