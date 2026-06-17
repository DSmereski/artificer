"""Auto-fact extractor helper (Phase 3.2).

Mem0-shaped post-turn extraction. Given the recent conversation, the
extractor emits a JSON delta of new user facts, preferences,
decisions, open tasks, and entities mentioned. The summarizer pipeline
folds those deltas into the per-thread `core_slots` so the planner
sees up-to-date memory on the next turn without the user ever having
to ask the bot to remember.

Quarantined like every other helper: nothing flows in besides the
recent messages and (optionally) the prior summary.
"""

from __future__ import annotations

from gateway.helpers.base import BaseHelper
from gateway.helpers.shapes import FactDelta


class FactExtractorHelper(BaseHelper):
    role = "fact_extractor"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", FactDelta)
        super().__init__(**kwargs)
