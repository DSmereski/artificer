# vault_writer/groomer/scanners/format_scanner.py
"""format_scanner — heading hierarchy + frontmatter sanity.

Lightweight checks; trivially-fixable items (trailing whitespace,
heading-level normalization, frontmatter ordering) are NOT flagged
here — they're applied directly by auto_fixers.py. format_scanner
emits suggestions only for issues that need human judgment (a note
with no h1 might be intentional; a note that skips h2→h4 might
indicate missing context).
"""
from __future__ import annotations

import re
from pathlib import Path

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.suggestion import (
    MAX_SUGGESTIONS_PER_SCAN,
    Suggestion,
)


name = "format_scanner"
kind = "format_scanner"

_HEADING_RE = re.compile(r"^(#+)\s+", re.MULTILINE)


def _slug(rel_path: str) -> str:
    base = rel_path.removesuffix(".md").replace("/", "_").replace(" ", "-")
    return base[:80] if len(base) > 80 else base


def scan(ctx: ScanContext) -> list[Suggestion]:
    out: list[Suggestion] = []
    for note in ctx.notes():
        if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
            break
        issues: list[str] = []
        levels = [len(m.group(1)) for m in _HEADING_RE.finditer(note.body)]
        if levels and levels[0] != 1:
            issues.append(f"Missing h1 — first heading is h{levels[0]}.")
        for prev, cur in zip(levels, levels[1:]):
            if cur > prev + 1:
                issues.append(
                    f"Skipped heading level: h{prev} → h{cur} "
                    "(insert intermediate heading)."
                )
                break
        if not issues:
            continue
        body = (
            f"Source: `{note.rel_path}`\n\n## Issues\n"
            + "\n".join(f"- {x}" for x in issues)
            + "\n\n## Recommended action\nFix headings manually.\n"
        )
        out.append(Suggestion(
            kind="format_scanner",
            slug=_slug(note.rel_path),
            confidence=0.85,
            title=f"Format issues: {note.rel_path}",
            body_md=body,
            refs=(note.rel_path,),
        ))
    return out


scan.kind = "format_scanner"
scan.name = "format_scanner"
