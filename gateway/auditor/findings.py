# gateway/auditor/findings.py
"""Finding model for the chat-log auditor.

Scanners emit Findings; the writer composes them into a per-hour audit
summary and escalates HIGH severity to vault/ops/escalations/.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


_KNOWN_KINDS = frozenset({
    "hallucination",
    "repeat_question",
    "unhandled_request",
    "security",
    "skill_gap",
    # Phase B (OpenSwarm import): saas_call without prior critic
    # delegation. Auditor flags so an injection-driven SaaS call surface
    # is observable post-hoc even if the runtime gate is bypassed.
    "composio_unconfirmed",
})


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: Severity
    turn_id: str
    bot: str
    summary: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _KNOWN_KINDS:
            raise ValueError(
                f"Finding.kind must be one of {sorted(_KNOWN_KINDS)}; "
                f"got {self.kind!r}"
            )

    def to_markdown_bullet(self) -> str:
        sev = self.severity.name.lower()
        return f"- **[{sev}]** Turn `{self.turn_id}` ({self.bot}): {self.summary}"
