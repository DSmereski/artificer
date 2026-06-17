# gateway/auditor/scanners/security.py
"""Pattern-based prompt-injection / jailbreak detector.

Scope: scans the verbatim ``user_msg`` for patterns. We do NOT scan
the model output here — that's the synthesizer's prompt-safety layer
(``gateway/prompt_safety.py::sanitise_helper_outputs``). This scanner
is the post-hoc audit trail: "did anyone try to break the bot last
hour?".

Severity:
- HIGH for clear-intent jailbreaks (ignore instructions, DAN, etc.)
- MEDIUM for soft role-overrides ("pretend you are…")
"""
from __future__ import annotations

import re

from gateway.auditor.findings import Finding, Severity


_HIGH_PATTERNS = [
    re.compile(r"\bignore\s+(all\s+)?(previous|prior)\s+instruction", re.IGNORECASE),
    re.compile(r"\b(disregard|forget)\s+(your|all|the)\s+(rules|instructions|prompt)", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+DAN\b", re.IGNORECASE),
    re.compile(r"\bjailbreak\b", re.IGNORECASE),
    re.compile(r"\bsystem\s*[:=]\s*", re.IGNORECASE),
    re.compile(r"</?(system|admin|root)>", re.IGNORECASE),
]

_MEDIUM_PATTERNS = [
    re.compile(r"\bpretend\s+you\s+are\b", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+if\s+you\s+have\s+no\b", re.IGNORECASE),
    re.compile(r"\bwithout\s+(any\s+)?restriction", re.IGNORECASE),
]


class SecurityScanner:
    name = "security"

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
            sev = self._severity(msg)
            if sev is None:
                continue
            out.append(Finding(
                kind="security",
                severity=sev,
                turn_id=str(t.get("turn_id", "?")),
                bot=str(t.get("bot", "")),
                summary=f"prompt-injection / jailbreak pattern detected ({sev.name})",
                detail=f"user_msg={msg[:200]!r}",
            ))
        return out

    @staticmethod
    def _severity(msg: str) -> Severity | None:
        for p in _HIGH_PATTERNS:
            if p.search(msg):
                return Severity.HIGH
        for p in _MEDIUM_PATTERNS:
            if p.search(msg):
                return Severity.MEDIUM
        return None
