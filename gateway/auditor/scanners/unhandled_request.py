# gateway/auditor/scanners/unhandled_request.py
"""Detect turns where the user asked for a concrete action and no
matching action verb fired.

Heuristic-only — recall over precision. False positives are tolerable
(the user can mark them as such); false negatives mean the system
silently ignored requests, which is the failure mode this scanner
exists to surface.
"""
from __future__ import annotations

import re

from gateway.auditor.findings import Finding, Severity


_PATTERNS: list[tuple[re.Pattern[str], frozenset[str], str]] = [
    (
        re.compile(
            r"\b(make|generate|render|draw|show|paint|create)\b.{0,40}\b"
            r"(picture|image|photo|portrait|render)\b",
            re.IGNORECASE,
        ),
        frozenset({"image_render", "generate_image"}),
        "image",
    ),
    (
        re.compile(
            r"\bremember\b.{0,40}\b(that|this|the)\b",
            re.IGNORECASE,
        ),
        frozenset({"vault_learn", "core_memory_replace", "core_memory_append"}),
        "remember",
    ),
    (
        re.compile(
            r"\b(remind|schedule|set\s+up\s+a\s+reminder)\b",
            re.IGNORECASE,
        ),
        frozenset({"calendar_add", "calendar.add"}),
        "reminder",
    ),
]


class UnhandledRequestScanner:
    name = "unhandled_request"

    def scan(
        self,
        *,
        turns: list[dict],
        memories: list[dict],
    ) -> list[Finding]:
        out: list[Finding] = []
        for t in turns:
            msg = t.get("user_msg") or ""
            if not isinstance(msg, str):
                continue
            actions = (t.get("synthesis") or {}).get("actions") or []
            verbs = {
                a.get("verb") for a in actions if isinstance(a, dict)
            }
            for pat, satisfying, label in _PATTERNS:
                if not pat.search(msg):
                    continue
                if verbs & satisfying:
                    continue
                out.append(Finding(
                    kind="unhandled_request",
                    severity=Severity.MEDIUM,
                    turn_id=str(t.get("turn_id", "?")),
                    bot=str(t.get("bot", "")),
                    summary=f"user asked for {label} but no matching action fired",
                    detail=f"user_msg={msg[:120]!r}; verbs={sorted(v for v in verbs if v)}",
                ))
                break  # one finding per turn
        return out
