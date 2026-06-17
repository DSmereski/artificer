# gateway/auditor/scanners/skill_gap.py
"""Detect turns where the user's words match a known skill domain
(image, calendar, vault), but the planner didn't delegate to a helper
in that domain.

Heuristic — relies on delegation-name conventions in the helper
catalog. False positives are acceptable; the value is in surfacing
"the planner didn't even try".
"""
from __future__ import annotations

import re

from gateway.auditor.findings import Finding, Severity


_SKILLS: list[tuple[re.Pattern[str], frozenset[str], str]] = [
    (
        re.compile(r"\b(picture|image|photo|render|portrait|paint)\b", re.IGNORECASE),
        frozenset({"image", "render", "imagegen"}),
        "image",
    ),
    (
        re.compile(r"\b(remind|schedule|tomorrow|next week|appointment)\b", re.IGNORECASE),
        frozenset({"calendar"}),
        "calendar",
    ),
    (
        re.compile(r"\b(remember|recall|what did we say|earlier you mentioned)\b", re.IGNORECASE),
        frozenset({"vault", "recall", "memory"}),
        "vault/recall",
    ),
]


class SkillGapScanner:
    name = "skill_gap"

    def scan(
        self,
        *,
        turns: list[dict],
        memories: list[dict],
    ) -> list[Finding]:
        out: list[Finding] = []
        for t in turns:
            msg = t.get("user_msg") or ""
            if not isinstance(msg, str) or not msg.strip():
                continue
            delegations = t.get("delegations") or []
            delegations_norm = " ".join(
                d.lower() for d in delegations if isinstance(d, str)
            )
            for pat, satisfying_substrs, label in _SKILLS:
                if not pat.search(msg):
                    continue
                if any(sub in delegations_norm for sub in satisfying_substrs):
                    continue
                out.append(Finding(
                    kind="skill_gap",
                    severity=Severity.LOW,
                    turn_id=str(t.get("turn_id", "?")),
                    bot=str(t.get("bot", "")),
                    summary=f"user message matched {label} skill domain "
                            f"but planner didn't delegate to a {label} helper",
                    detail=f"user_msg={msg[:120]!r}; delegations={delegations}",
                ))
                break
        return out
