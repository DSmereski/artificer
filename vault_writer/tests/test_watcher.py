"""Tests for vault_writer.watcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from vault_writer.watcher import NoteContent, parse_note


def test_parse_note_valid_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "canon" / "maggy.md"
    f.parent.mkdir()
    f.write_text(
        """---
type: canon
author: human
audience: [all]
tags: [character, bot]
---

# Maggy

Body line.
""",
        encoding="utf-8",
    )
    note = parse_note(f, tmp_path)
    assert isinstance(note, NoteContent)
    assert note.rel_path == "canon/maggy.md"
    assert note.note_type == "canon"
    assert note.author == "human"
    assert note.audience == ("all",)
    assert note.frontmatter["tags"] == ["character", "bot"]
    assert "# Maggy" in note.body
    assert "Body line." in note.body


def test_parse_note_missing_frontmatter_returns_defaults(tmp_path: Path) -> None:
    f = tmp_path / "people" / "noone.md"
    f.parent.mkdir()
    f.write_text("Just a body with no frontmatter.\n", encoding="utf-8")
    note = parse_note(f, tmp_path)
    assert note.note_type == "person"
    assert note.author == "unknown"
    assert note.audience == ("all",)
    assert note.body == "Just a body with no frontmatter.\n"


def test_parse_note_audience_string_coerced_to_list(tmp_path: Path) -> None:
    f = tmp_path / "ops" / "x.md"
    f.parent.mkdir()
    f.write_text(
        """---
type: ops
author: claude-code
audience: claude-code
---
Body.
""",
        encoding="utf-8",
    )
    note = parse_note(f, tmp_path)
    assert note.audience == ("claude-code",)


def test_parse_note_malformed_audience_fails_closed(tmp_path: Path) -> None:
    f = tmp_path / "ops" / "y.md"
    f.parent.mkdir()
    f.write_text(
        """---
type: ops
audience: {oops: wrong}
---
body
""",
        encoding="utf-8",
    )
    note = parse_note(f, tmp_path)
    # Malformed audience should NOT widen to ["all"] — fail closed.
    assert note.audience == ("__malformed__",)


def test_parse_note_oversized_raises(tmp_path: Path) -> None:
    from vault_writer.util import MAX_NOTE_FILE_BYTES
    from vault_writer.watcher import NoteTooLarge

    f = tmp_path / "canon" / "huge.md"
    f.parent.mkdir()
    f.write_text("a" * (MAX_NOTE_FILE_BYTES + 1), encoding="utf-8")
    with pytest.raises(NoteTooLarge):
        parse_note(f, tmp_path)


def test_parse_note_outside_vault_raises(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other_root = tmp_path_factory.mktemp("other")
    (other_root / "x.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="outside vault"):
        parse_note(other_root / "x.md", tmp_path)
