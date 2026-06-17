# vault_writer/groomer/auto_fixers.py
"""Direct-apply trivial fixes — no suggestion overhead.

Two fixes ship in Phase 3:
1. Trailing whitespace on each line.
2. CRLF → LF line endings.

Both are idempotent and pure-text. Heading-level renormalization and
frontmatter key-ordering are intentionally NOT shipped in v1 because
they have legitimate edge cases (intentional skips; convention
disagreements about key order) — they belong in suggestions, not
auto-apply.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from shared.atomic_write import atomic_write_text
from vault_writer.groomer.inputs import should_skip_path

log = logging.getLogger("vault_writer.groomer.auto_fixers")


@dataclass
class AutoFixResult:
    files_scanned: int = 0
    files_changed: int = 0
    paths_changed: list[str] = field(default_factory=list)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    fixed: list[str] = []
    in_fence = False
    for line in lines:
        # Treat ``` (any info string) as a fence toggle. Whitespace
        # inside fenced code blocks can be load-bearing (e.g., demos
        # of trailing-space bugs), so we leave it untouched.
        if line.lstrip().startswith("```"):
            fixed.append(line.rstrip(" \t"))
            in_fence = not in_fence
            continue
        if in_fence:
            fixed.append(line)
        else:
            fixed.append(line.rstrip(" \t"))
    return "\n".join(fixed)


def apply_auto_fixes(vault_path: Path) -> AutoFixResult:
    res = AutoFixResult()
    if not vault_path.exists():
        return res
    for p in vault_path.rglob("*.md"):
        rel = p.relative_to(vault_path)
        if should_skip_path(rel.parts):
            continue
        try:
            raw = p.read_bytes()
            original = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        res.files_scanned += 1
        normalised = _normalise(original)
        if normalised.encode("utf-8") == raw:
            continue
        try:
            atomic_write_text(p, normalised)
            res.files_changed += 1
            res.paths_changed.append(str(rel).replace("\\", "/"))
        except OSError as e:
            log.warning("auto_fix write failed for %s: %s", p, e)
    return res
