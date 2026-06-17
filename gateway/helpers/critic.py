"""Critic helper — gates risky synthesis actions (vault writes, image
renders, ntfy pushes, skill creation).

M2.2 ships the LLM-call shell. M6.1 makes it a HARD gate inside the
HiveCoordinator: if Critic returns block=True, the synthesis action
is suppressed and the user gets the rationale.
"""

from __future__ import annotations

from gateway.helpers.base import BaseHelper
from gateway.helpers.shapes import CriticReport


class CriticHelper(BaseHelper):
    role = "critic"

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("schema", CriticReport)
        super().__init__(**kwargs)
