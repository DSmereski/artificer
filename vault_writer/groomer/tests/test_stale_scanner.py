# vault_writer/groomer/tests/test_stale_scanner.py
"""Tests for stale_scanner — old + unreferenced notes."""
from __future__ import annotations

import os
import time
from pathlib import Path

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.scanners.stale_scanner import scan


def _write(p: Path, body: str, mtime: float | None = None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def test_flags_old_note(tmp_path: Path) -> None:
    now = time.time()
    seven_months_ago = now - (60 * 60 * 24 * 30 * 7)
    _write(tmp_path / "old.md", "stale", mtime=seven_months_ago)
    _write(tmp_path / "fresh.md", "new", mtime=now - 60)
    ctx = ScanContext(vault_path=tmp_path, now_ts=now)
    out = scan(ctx)
    rels = {s.refs[0] for s in out}
    assert "old.md" in rels
    assert "fresh.md" not in rels


def test_skips_canon_dir(tmp_path: Path) -> None:
    # canon/ is hand-curated; never flagged stale.
    now = time.time()
    seven_months_ago = now - (60 * 60 * 24 * 30 * 7)
    _write(tmp_path / "canon" / "claude-code.md", "x", mtime=seven_months_ago)
    ctx = ScanContext(vault_path=tmp_path, now_ts=now)
    assert scan(ctx) == []


def test_no_old_notes_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path / "n.md", "fresh", mtime=time.time() - 60)
    ctx = ScanContext(vault_path=tmp_path, now_ts=time.time())
    assert scan(ctx) == []


def test_different_dirs_same_stem_produce_different_slugs(tmp_path: Path) -> None:
    """people/alice.md and projects/alice.md must not collide on slug 'alice'."""
    now = time.time()
    seven_months_ago = now - (60 * 60 * 24 * 30 * 7)
    _write(tmp_path / "people" / "alice.md", "stale", mtime=seven_months_ago)
    _write(tmp_path / "projects" / "alice.md", "stale", mtime=seven_months_ago)
    ctx = ScanContext(vault_path=tmp_path, now_ts=now)
    out = scan(ctx)
    assert len(out) == 2
    slugs = {s.slug for s in out}
    assert len(slugs) == 2, f"Expected 2 distinct slugs, got: {slugs}"
