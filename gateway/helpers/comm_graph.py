"""Allowed planner → helper delegation edges.

Defense-in-depth registry. The planner emits a list of helper
delegations and the coordinator dispatches them; without this gate, a
prompt-injected vault note that influences the planner's JSON output
could request *any* helper. This module fixes the set of helpers
planner is allowed to call so injection can't smuggle in unexpected
roles (e.g., spinning up the synthesizer mid-plan or invoking critic
to bypass its own gate).

Source-of-truth: this list mirrors the helpers the planner prompt
documents as available. If you add a helper the planner should be
able to call, add it here too. The coordinator emits a structured
log + drops the delegation when an edge is missing — never raises so
one bad planner output doesn't kill the turn.

Why static? Phase D of the OpenSwarm import (declarative comm graph).
Per-context overrides (e.g., audience-aware deny lists) belong in the
audience layer, not here.
"""

from __future__ import annotations


# Mapping: caller role → set of callee roles it may delegate to.
# Currently only the planner emits delegations — helpers don't call
# helpers in this codebase. The single-key shape leaves room for
# future helpers (e.g., a meta-planner) without an API change.
ALLOWED_EDGES: dict[str, frozenset[str]] = {
    "planner": frozenset({
        "researcher",
        "librarian",
        "chat_recall",
        "coder",
        "image_director",
        "sysmon",
        "summarizer",
        "skill_runner",
        "fact_extractor",
    }),
}


def is_allowed(caller: str, callee: str) -> bool:
    """Return True iff caller may delegate to callee."""
    return callee in ALLOWED_EDGES.get(caller, frozenset())


__all__ = ["ALLOWED_EDGES", "is_allowed"]
