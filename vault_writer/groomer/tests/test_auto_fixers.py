# vault_writer/groomer/tests/test_auto_fixers.py
"""Tests for auto_fixers — direct-apply trivial fixes."""
from __future__ import annotations

from pathlib import Path

from vault_writer.groomer.auto_fixers import (
    AutoFixResult,
    apply_auto_fixes,
)


def test_strips_trailing_whitespace(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_text("line a   \nline b\t\nline c\n", encoding="utf-8")
    res = apply_auto_fixes(tmp_path)
    assert isinstance(res, AutoFixResult)
    assert res.files_changed == 1
    assert p.read_text(encoding="utf-8") == "line a\nline b\nline c\n"


def test_normalises_crlf(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"a\r\nb\r\n")
    res = apply_auto_fixes(tmp_path)
    assert res.files_changed == 1
    assert p.read_bytes() == b"a\nb\n"


def test_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "n.md"
    p.write_bytes(b"clean\nfile\n")  # LF already normalised — no change expected
    res = apply_auto_fixes(tmp_path)
    assert res.files_changed == 0


def test_skips_ops_dir(tmp_path: Path) -> None:
    # Don't fix our own outputs.
    p = tmp_path / "ops" / "groomer" / "x.md"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"trailing   \n")
    apply_auto_fixes(tmp_path)
    assert b"trailing   " in p.read_bytes()


def test_preserves_whitespace_inside_fenced_code_blocks(tmp_path: Path) -> None:
    """Inside a ``` fence, trailing whitespace can be load-bearing
    (e.g., demonstrating a trailing-space bug). Strip outside fences only."""
    p = tmp_path / "n.md"
    src = (
        "prose line   \n"
        "```python\n"
        "x = 1   \n"
        "y = 2\t\n"
        "```\n"
        "more prose   \n"
    )
    p.write_text(src, encoding="utf-8")
    apply_auto_fixes(tmp_path)
    out = p.read_text(encoding="utf-8")
    expected = (
        "prose line\n"
        "```python\n"
        "x = 1   \n"
        "y = 2\t\n"
        "```\n"
        "more prose\n"
    )
    assert out == expected
