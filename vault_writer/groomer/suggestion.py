"""Suggestion model + scanner registry.

The groomer's Phase 2 (auditor) cousin had four places where a new
finding-kind had to be added in lockstep: the scanner module, the
audit_run default list, the findings_writer label map, and the
findings.py KNOWN_KINDS frozenset. We collapse all of that here.
A new scanner adds itself to REGISTRY at module import time.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

# Five scanners, fixed ordering. New scanners append, never reorder
# (the suggestions writer renders sections in this order).
KINDS: tuple[str, ...] = (
    "dup_scanner",
    "link_scanner",
    "format_scanner",
    "contradiction_scanner",
    "stale_scanner",
)

REGISTRY: dict[str, str] = {
    "dup_scanner":           "Possible duplicates",
    "link_scanner":          "Broken wikilinks",
    "format_scanner":        "Format issues",
    "contradiction_scanner": "Contradictions",
    "stale_scanner":         "Stale notes",
}


def label_for(kind: str) -> str:
    return REGISTRY[kind]


class Confidence(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class Suggestion:
    """One groomer proposal. Materialised to ops/groomer/<kind>/<slug>.md.
    Never auto-applied — these are proposals for human review."""
    kind: str
    slug: str           # path-safe identifier (e.g., "penguin")
    confidence: float   # 0.0–1.0
    title: str
    body_md: str        # rendered markdown body
    # Optional context the writer may include in frontmatter:
    refs: tuple[str, ...] = ()    # related vault paths

    def __post_init__(self) -> None:
        if self.kind not in REGISTRY:
            raise ValueError(f"unknown kind: {self.kind}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence {self.confidence} out of [0,1]"
            )


# Soft cap so a runaway scanner (e.g., dup_scanner returning N**2 pairs)
# can't drown the suggestions writer. The opus reviewer flagged this
# as a Phase 3 prep recommendation. Scanners enforce this themselves —
# the protocol is advisory, but groom_run also enforces it as a belt-
# and-braces check before writing.
MAX_SUGGESTIONS_PER_SCAN: int = 500

# Global per-run cap across ALL scanners. Even with each scanner
# capped at 500, five scanners could emit 2500 proposals — too many
# for a human to triage. This trims the lowest-confidence tail so the
# user sees the most actionable items first.
MAX_SUGGESTIONS_PER_RUN: int = 200
