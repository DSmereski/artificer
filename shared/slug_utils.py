"""Shared slug helpers used by both gateway and vault_writer.

Both subsystems need to reverse a slug back to a display title (for
autolink lookup, search hits, planner prompts, etc.). Keeping two
implementations risked silent drift — and the daemon doesn't currently
store the display title in frontmatter, so this is the only reliable
cross-note title source either side has.
"""

from __future__ import annotations


def title_from_slug(stem: str) -> str:
    """'kraken-star-citizen-ship' → 'Kraken Star Citizen Ship'.

    Empty / hyphen-only inputs fall through unchanged so the caller can
    distinguish "no useful title" from "empty title".
    """
    parts = [p for p in stem.split("-") if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) if parts else stem
