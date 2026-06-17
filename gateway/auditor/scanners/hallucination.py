# gateway/auditor/scanners/hallucination.py
"""Contradiction detector between the assistant's reply and the user's
persisted memory facts.

A user fact is shaped ``"key: value"`` (the convention written by
Mem0-style auto-extraction). This scanner pulls each ``key`` and
checks the reply for any *different* value-noun in proximity to the
key, flagging it as a contradiction.

False-positive-tolerant. Real cross-vault checking will land in a
follow-up that uses ``VaultClient.search_notes``.
"""
from __future__ import annotations

import re

from gateway.auditor.findings import Finding, Severity


_FACT_RE = re.compile(r"^\s*([A-Za-z][\w\s\-]{0,40}?)\s*[:=]\s*(.+?)\s*$")


def _facts_for_user(memories: list[dict], user_id: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in memories:
        if m.get("user_id") != user_id:
            continue
        for raw in m.get("user_facts") or []:
            if not isinstance(raw, str):
                continue
            mo = _FACT_RE.match(raw)
            if mo:
                out.append((mo.group(1).strip().lower(), mo.group(2).strip().lower()))
    return out


def _reply_contradicts(reply: str, key: str, expected_value: str) -> str | None:
    """If the reply mentions the key in a way that asserts a *different*
    value than ``expected_value``, return the asserted value. Else None.
    """
    if not reply or not key:
        return None
    pat = re.compile(
        rf"\b{re.escape(key)}\b\s*(?:is|=|:)?\s*([A-Za-z][\w\-]{{1,30}})",
        re.IGNORECASE,
    )
    m = pat.search(reply)
    if not m:
        return None
    asserted = m.group(1).strip().lower()
    if asserted and asserted != expected_value:
        if asserted in {"a", "the", "an", "your", "my", "actually", "probably"}:
            return None
        return asserted
    return None


class HallucinationScanner:
    name = "hallucination"

    def scan(
        self,
        *,
        turns: list[dict],
        memories: list[dict],
    ) -> list[Finding]:
        out: list[Finding] = []
        for t in turns:
            reply = (t.get("synthesis") or {}).get("reply") or t.get("final_reply") or ""
            if not isinstance(reply, str) or not reply.strip():
                continue
            user_id = t.get("user_id")
            if not isinstance(user_id, int):
                continue
            facts = _facts_for_user(memories, user_id)
            for key, expected in facts:
                asserted = _reply_contradicts(reply, key, expected)
                if asserted is None:
                    continue
                out.append(Finding(
                    kind="hallucination",
                    severity=Severity.HIGH,
                    turn_id=str(t.get("turn_id", "?")),
                    bot=str(t.get("bot", "")),
                    summary=f"reply asserts {key}={asserted!r} but memory says {key}={expected!r}",
                    detail=f"key={key!r} asserted={asserted!r} expected={expected!r}",
                ))
                break  # one finding per turn
        return out
