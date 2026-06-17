# gateway/auditor/scanners/repeat_question.py
"""Detect when the user asks the same thing in multiple turns within
the audit window. Repeats are a strong signal that the prior reply
didn't satisfy the question.

Severity: LOW for 2 occurrences, MEDIUM for 3+.
"""
from __future__ import annotations

import re
from collections import defaultdict

from gateway.auditor.findings import Finding, Severity


_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


class RepeatQuestionScanner:
    name = "repeat_question"

    def scan(
        self,
        *,
        turns: list[dict],
        memories: list[dict],
    ) -> list[Finding]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in turns:
            msg = t.get("user_msg") or ""
            if not isinstance(msg, str) or not msg.strip():
                continue
            groups[_normalize(msg)].append(t)
        out: list[Finding] = []
        for norm, ts in groups.items():
            if len(ts) < 2:
                continue
            sev = Severity.MEDIUM if len(ts) >= 3 else Severity.LOW
            ids = ", ".join(t.get("turn_id", "?") for t in ts)
            first = ts[0]
            out.append(Finding(
                kind="repeat_question",
                severity=sev,
                turn_id=str(first.get("turn_id", "?")),
                bot=str(first.get("bot", "")),
                summary=f"user asked the same question {len(ts)} times: "
                        f"{first.get('user_msg', '')[:80]!r}",
                detail=f"turn_ids: {ids}",
            ))
        return out
