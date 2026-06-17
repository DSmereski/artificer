# vault_writer/groomer/inputs.py
"""Iterate vault notes; split frontmatter from body.

Reuses the same skip rules vault_writer.daemon already applies for
indexing: anything under a dotdir (`.obsidian/`, `.git/`) or under
`ops/` (auditor outputs, escalations, groomer outputs) is excluded —
otherwise the groomer would feed its own outputs back into itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from vault_writer.util import MAX_NOTE_FILE_BYTES


log = logging.getLogger("vault_writer.groomer.inputs")

_SKIP_TOP_DIRS = ("ops",)


def should_skip_path(rel_parts: tuple[str, ...]) -> bool:
    """Single source of truth for groomer path-skip rules.

    Used by both the read-only walker (iter_vault_notes) and the
    auto-apply path (auto_fixers.apply_auto_fixes) so the two never
    drift apart and start scanning notes the other refuses to touch.
    """
    if any(part.startswith(".") for part in rel_parts):
        return True
    if rel_parts and rel_parts[0] in _SKIP_TOP_DIRS:
        return True
    return False


@dataclass
class NoteRecord:
    rel_path: str           # forward-slash, relative to vault root
    abs_path: Path
    mtime: float
    body: str               # post-frontmatter
    frontmatter: dict[str, Any] = field(default_factory=dict)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Obsidian-style YAML frontmatter parser. Falls back to ({}, text)
    on any failure so a malformed header never crashes the groomer."""
    if not text.startswith("---"):
        return {}, text
    # split off the first ``---`` line, then look for closing ``---``.
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return {}, text
    yaml_block = rest[:end]
    body_start = end + len("\n---")
    if rest[body_start:body_start + 1] == "\n":
        body_start += 1
    body = rest[body_start:]
    try:
        fm = yaml.safe_load(yaml_block) or {}
        if not isinstance(fm, dict):
            return {}, text
    except Exception:  # noqa: BLE001
        return {}, text
    return fm, body


def iter_vault_notes(vault_path: Path) -> Iterator[NoteRecord]:
    """Walk the vault, yielding one NoteRecord per markdown file."""
    if not vault_path.exists():
        return
    for p in vault_path.rglob("*.md"):
        rel = p.relative_to(vault_path)
        if should_skip_path(rel.parts):
            continue
        try:
            st = p.stat()
            if st.st_size > MAX_NOTE_FILE_BYTES:
                log.warning(
                    "groomer skipping %s: %d bytes exceeds MAX_NOTE_FILE_BYTES",
                    rel, st.st_size,
                )
                continue
            text = p.read_text(encoding="utf-8")
            mtime = st.st_mtime
        except OSError:
            continue
        fm, body = split_frontmatter(text)
        yield NoteRecord(
            rel_path=str(rel).replace("\\", "/"),
            abs_path=p,
            mtime=mtime,
            body=body,
            frontmatter=fm,
        )
