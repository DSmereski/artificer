# vault_writer/groomer/scanners/stale_scanner.py
"""stale_scanner — notes whose mtime is > 6 months ago.

Phase 3 ships with an mtime-only signal. Adding the FTS5 query-log
check (was the note retrieved by ANY search in the last 30 days)
needs a query-log table that doesn't exist yet — that's a follow-up.
For now confidence stays at 0.5 to reflect the weak signal.

`canon/` is the hand-curated "immutable, human-only" tier and is
never flagged stale even when ancient — that's the user's permanent
canonical record.
"""
from __future__ import annotations

from pathlib import Path

from vault_writer.groomer.scanners import ScanContext
from vault_writer.groomer.suggestion import (
    MAX_SUGGESTIONS_PER_SCAN,
    Suggestion,
)


name = "stale_scanner"
kind = "stale_scanner"

# 180 days. Named "_SIX_MONTHS_S" loosely — calendar months average
# ~30.4 days, not 30, so this trips ~3 days early at the half-year
# mark. Acceptable for a soft "looks neglected" signal that the user
# is the final arbiter on.
_STALE_THRESHOLD_S = 60 * 60 * 24 * 180
_NEVER_STALE_TOP_DIRS = ("canon",)


def _slug(rel_path: str) -> str:
    base = rel_path.removesuffix(".md").replace("/", "_").replace(" ", "-")
    return base[:80] if len(base) > 80 else base


def scan(ctx: ScanContext) -> list[Suggestion]:
    out: list[Suggestion] = []
    cutoff = ctx.now_ts - _STALE_THRESHOLD_S
    for note in ctx.notes():
        if len(out) >= MAX_SUGGESTIONS_PER_SCAN:
            break
        parts = note.rel_path.split("/")
        if parts and parts[0] in _NEVER_STALE_TOP_DIRS:
            continue
        if note.mtime >= cutoff:
            continue
        days_old = int((ctx.now_ts - note.mtime) / 86400)
        body = (
            f"Source: `{note.rel_path}`\n"
            f"Last modified: ~{days_old} days ago\n\n"
            "## Recommended action\n"
            "Review for archival or deletion. If still useful, touch the\n"
            "file or add a recent journal cross-reference.\n"
        )
        out.append(Suggestion(
            kind="stale_scanner",
            slug=_slug(note.rel_path),
            confidence=0.5,
            title=f"Stale: {note.rel_path}",
            body_md=body,
            refs=(note.rel_path,),
        ))
    return out


scan.kind = "stale_scanner"
scan.name = "stale_scanner"
