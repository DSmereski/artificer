# gateway/auditor/tests/test_skill_gap_scanner.py
"""Tests for the skill-gap scanner."""
from __future__ import annotations

from gateway.auditor.findings import Severity
from gateway.auditor.scanners.skill_gap import SkillGapScanner


def _turn(*, turn_id: str, user_msg: str, delegations: list[str]) -> dict:
    return {
        "ts": 0,
        "turn_id": turn_id,
        "bot": "hive",
        "user_msg": user_msg,
        "user_id": 1,
        "delegations": delegations,
    }


def test_calendar_keyword_used_calendar_skill_no_finding() -> None:
    s = SkillGapScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t1",
            user_msg="schedule a reminder tomorrow at 9",
            delegations=["calendar_planner"],
        )],
        memories=[],
    )
    assert out == []


def test_calendar_keyword_used_no_relevant_skill_emits_finding() -> None:
    s = SkillGapScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t1",
            user_msg="schedule a reminder tomorrow at 9",
            delegations=["chat_recall"],
        )],
        memories=[],
    )
    assert len(out) == 1
    f = out[0]
    assert f.kind == "skill_gap"
    assert f.severity == Severity.LOW
    assert "calendar" in f.summary.lower()


def test_no_delegations_with_relevant_keyword_emits_finding() -> None:
    s = SkillGapScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t1",
            user_msg="please render a picture of a sunset",
            delegations=[],
        )],
        memories=[],
    )
    assert len(out) == 1
    assert "image" in out[0].summary.lower() or "picture" in out[0].summary.lower()


def test_unrelated_message_no_finding() -> None:
    s = SkillGapScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t1",
            user_msg="hello, how are you?",
            delegations=[],
        )],
        memories=[],
    )
    assert out == []
