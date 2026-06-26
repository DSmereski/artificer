# gateway/auditor/tests/test_hallucination_scanner.py
"""Tests for the hallucination scanner.

Phase-2 shape: contradiction between synthesizer reply and the user's
persisted memory (user_facts, decisions). Vault canon cross-check is
a Phase-2 follow-up.
"""
from __future__ import annotations

from gateway.auditor.findings import Severity
from gateway.auditor.scanners.hallucination import HallucinationScanner


def _turn(*, turn_id: str, reply: str, user_id: int = 1) -> dict:
    return {
        "ts": 0,
        "turn_id": turn_id,
        "bot": "hive",
        "user_id": user_id,
        "user_msg": "what's my favorite color?",
        "synthesis": {"reply": reply, "actions": []},
        "final_reply": reply,
    }


def _memory(*, user_id: int, user_facts: list[str]) -> dict:
    return {
        "bot": "hive",
        "user_id": user_id,
        "thread_id": "default",
        "user_facts": user_facts,
        "decisions": [],
        "mid_summary": "",
    }


def test_reply_consistent_with_facts_no_finding() -> None:
    s = HallucinationScanner()
    out = s.scan(
        turns=[_turn(turn_id="t1", reply="Your favorite color is red.")],
        memories=[_memory(user_id=1, user_facts=["favorite color: red"])],
    )
    assert out == []


def test_reply_contradicts_user_fact_emits_high_finding() -> None:
    s = HallucinationScanner()
    out = s.scan(
        turns=[_turn(turn_id="t1", reply="Your favorite color is blue.")],
        memories=[_memory(user_id=1, user_facts=["favorite color: red"])],
    )
    assert len(out) == 1
    f = out[0]
    assert f.kind == "hallucination"
    assert f.severity == Severity.HIGH
    assert "blue" in f.detail.lower()
    assert "red" in f.detail.lower()


def test_no_memory_no_finding() -> None:
    s = HallucinationScanner()
    out = s.scan(
        turns=[_turn(turn_id="t1", reply="The sky is blue.")],
        memories=[],
    )
    assert out == []


def test_only_checks_facts_with_color_keyword_pattern() -> None:
    """Sanity: scanner uses simple key:value heuristics; unrelated
    facts don't trigger contradictions on unrelated replies."""
    s = HallucinationScanner()
    out = s.scan(
        turns=[_turn(turn_id="t1", reply="The kraken is a sea creature.")],
        memories=[_memory(user_id=1, user_facts=["favorite color: red"])],
    )
    assert out == []
