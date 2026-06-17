# vault_writer/groomer/tests/test_suggestions_writer.py
"""Tests for the suggestions filesystem writer."""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from vault_writer.groomer.suggestion import Suggestion
from vault_writer.groomer.suggestions_writer import (
    _render_refs,
    _render_suggestion,
    write_suggestions,
)


def test_writes_one_file_per_suggestion(tmp_path: Path) -> None:
    sugs = [
        Suggestion(kind="dup_scanner", slug="penguin", confidence=0.94,
                   title="dup", body_md="body"),
        Suggestion(kind="link_scanner", slug="x__missing", confidence=0.95,
                   title="link", body_md="body"),
    ]
    res = write_suggestions(vault_path=tmp_path, suggestions=sugs,
                            now_ts=1714564800.0,
                            counts_by_kind={"dup_scanner": 1, "link_scanner": 1})
    assert res.files_written == 2
    assert (tmp_path / "ops" / "groomer" / "dup_scanner" / "penguin.md").exists()
    assert (tmp_path / "ops" / "groomer" / "link_scanner" / "x__missing.md").exists()


def test_writes_run_summary(tmp_path: Path) -> None:
    res = write_suggestions(vault_path=tmp_path, suggestions=[],
                            now_ts=1714564800.0,
                            counts_by_kind={"dup_scanner": 0})
    runs_dir = tmp_path / "ops" / "groomer" / "_runs"
    assert runs_dir.exists()
    assert any(p.suffix == ".md" for p in runs_dir.iterdir())


def test_idempotent_same_body(tmp_path: Path) -> None:
    s = Suggestion(kind="dup_scanner", slug="x", confidence=0.94,
                   title="t", body_md="body")
    r1 = write_suggestions(vault_path=tmp_path, suggestions=[s],
                           now_ts=1.0, counts_by_kind={"dup_scanner": 1})
    r2 = write_suggestions(vault_path=tmp_path, suggestions=[s],
                           now_ts=2.0, counts_by_kind={"dup_scanner": 1})
    assert r1.files_written == 1
    # Second run sees identical body — no rewrite, but still writes a run summary.
    assert r2.files_unchanged == 1


def test_overwrites_changed_body(tmp_path: Path) -> None:
    s1 = Suggestion(kind="dup_scanner", slug="x", confidence=0.94,
                    title="t", body_md="body v1")
    s2 = Suggestion(kind="dup_scanner", slug="x", confidence=0.95,
                    title="t", body_md="body v2")
    write_suggestions(vault_path=tmp_path, suggestions=[s1],
                      now_ts=1.0, counts_by_kind={"dup_scanner": 1})
    write_suggestions(vault_path=tmp_path, suggestions=[s2],
                      now_ts=2.0, counts_by_kind={"dup_scanner": 1})
    body = (tmp_path / "ops" / "groomer" / "dup_scanner" / "x.md").read_text(encoding="utf-8")
    assert "body v2" in body


# ---------------------------------------------------------------------------
# Fix 1: YAML injection — refs must round-trip cleanly through yaml.safe_load
# ---------------------------------------------------------------------------

def _parse_frontmatter(rendered: str) -> dict:
    """Extract and parse the YAML frontmatter block from a rendered suggestion."""
    assert rendered.startswith("---\n"), "Expected frontmatter delimiter"
    end = rendered.index("\n---\n", 4)
    return yaml.safe_load(rendered[4:end])


def test_refs_roundtrip_with_special_chars() -> None:
    """Refs containing commas, brackets, colons, and hashes must round-trip."""
    special_refs = ("foo, bar.md", "has [brackets].md", "colon:path.md", "#hash.md")
    s = Suggestion(
        kind="stale_scanner",
        slug="test-special",
        confidence=0.5,
        title="Test",
        body_md="body\n",
        refs=special_refs,
    )
    rendered = _render_suggestion(s)
    fm = _parse_frontmatter(rendered)
    assert fm["refs"] == list(special_refs)


def test_refs_empty_renders_inline_empty_list() -> None:
    """Empty refs must render as `refs: []` (unambiguous YAML)."""
    result = _render_refs(())
    assert result == "refs: []"
    parsed = yaml.safe_load(result)
    assert parsed["refs"] == []


def test_refs_with_backslash_roundtrips() -> None:
    """Backslashes inside refs must be escaped so YAML round-trips cleanly."""
    s = Suggestion(
        kind="stale_scanner",
        slug="test-backslash",
        confidence=0.5,
        title="Test",
        body_md="body\n",
        refs=('path\\with\\backslash.md',),
    )
    rendered = _render_suggestion(s)
    fm = _parse_frontmatter(rendered)
    assert fm["refs"] == ['path\\with\\backslash.md']


def test_refs_with_embedded_quotes_roundtrips() -> None:
    """Double-quotes inside a ref must be escaped so YAML round-trips cleanly."""
    s = Suggestion(
        kind="stale_scanner",
        slug="test-quotes",
        confidence=0.5,
        title="Test",
        body_md="body\n",
        refs=('say "hello".md',),
    )
    rendered = _render_suggestion(s)
    fm = _parse_frontmatter(rendered)
    assert fm["refs"] == ['say "hello".md']


def test_detected_at_preserved_on_reemit(tmp_path: Path) -> None:
    """The detected_at timestamp records first-detection; a later run that
    re-emits the same proposal must NOT slide the timestamp forward."""
    s = Suggestion(kind="dup_scanner", slug="x", confidence=0.94,
                   title="t", body_md="body")
    write_suggestions(vault_path=tmp_path, suggestions=[s],
                      now_ts=1000.0, counts_by_kind={"dup_scanner": 1})
    p = tmp_path / "ops" / "groomer" / "dup_scanner" / "x.md"
    first = p.read_text(encoding="utf-8")
    write_suggestions(vault_path=tmp_path, suggestions=[s],
                      now_ts=99999.0, counts_by_kind={"dup_scanner": 1})
    second = p.read_text(encoding="utf-8")
    assert first == second
    assert "detected_at: 1970-01-01T00:16:40Z" in second


def test_detected_at_emitted_when_supplied(tmp_path: Path) -> None:
    """A first-time write stamps detected_at into the frontmatter."""
    s = Suggestion(kind="dup_scanner", slug="x", confidence=0.94,
                   title="t", body_md="body")
    write_suggestions(vault_path=tmp_path, suggestions=[s],
                      now_ts=1714564800.0,
                      counts_by_kind={"dup_scanner": 1})
    p = tmp_path / "ops" / "groomer" / "dup_scanner" / "x.md"
    fm = _parse_frontmatter(p.read_text(encoding="utf-8"))
    assert "detected_at" in fm
