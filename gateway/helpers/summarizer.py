"""Summarizer helper (skeleton).

M2.2: stub that compresses a list of messages into a Summary shape.
M5.2 wires it into conversation_memory for the tiered context layer.
"""

from __future__ import annotations

from gateway.helpers.base import BaseHelper
from gateway.helpers.shapes import Summary


class SummarizerHelper(BaseHelper):
    role = "summarizer"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", Summary)
        super().__init__(**kwargs)
