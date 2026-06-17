# vault_writer/groomer/scanners/link_scanner.py
"""link_scanner — flags [[wikilinks]] whose target file is missing.

Reuses vault_writer.util.extract_wikilinks for parsing, and walks the
vault once to build the set of resolvable targets. Resolution rules:
- Bare name: `[[Note]]` matches any `.md` whose stem is `Note`
  (case-sensitive, mirroring Obsidian).
- With folder: `[[folder/Note]]` matches the exact relative path.
"""
from __future__ import annotations

import time
from pathlib import Path

from vault_writer.groomer.inputs import NoteRecord
from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.suggestion import (
    MAX_SUGGESTIONS_PER_SCAN,
    Suggestion,
)
from vault_writer.util import extract_wikilinks


name = "link_scanner"
kind = "link_scanner"


def _build_target_index(notes: list[NoteRecord]) -> set[str]:
    """Return set of resolvable wikilink targets (stems + relative paths)."""
    targets: set[str] = set()
    for note in notes:
        # bare stem
        stem = Path(note.rel_path).stem
        targets.add(stem)
        # full relative path without .md
        rel_no_ext = note.rel_path[:-3] if note.rel_path.endswith(".md") else note.rel_path
        targets.add(rel_no_ext)
    return targets


def _slug(source_rel: str, target: str) -> str:
    src = source_rel.removesuffix(".md").replace("/", "_").replace(" ", "-")
    tgt = target.replace("/", "_").replace(" ", "-")
    combined = f"{src}--{tgt}"
    return combined[:80] if len(combined) > 80 else combined


def scan(ctx: ScanContext) -> list[Suggestion]:
    notes = ctx.notes()
    targets = _build_target_index(notes)
    out: list[Suggestion] = []
    for note in notes:
        if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
            break
        links = extract_wikilinks(note.body)
        for link in links:
            if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
                break
            if link in targets:
                continue
            body = (
                f"Source: `{note.rel_path}`\n"
                f"Broken link: `[[{link}]]`\n\n"
                "## Recommended action\n"
                "Either fix the link target or remove the wikilink.\n"
            )
            out.append(Suggestion(
                kind="link_scanner",
                slug=_slug(note.rel_path, link),
                confidence=0.95,
                title=f"Broken wikilink: {note.rel_path} → [[{link}]]",
                body_md=body,
                refs=(note.rel_path,),
            ))
    return out


scan.kind = "link_scanner"
scan.name = "link_scanner"
