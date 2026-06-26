# gateway/auditor/tests/test_repeat_question_scanner.py
"""Tests for the repeat-question scanner."""
from __future__ import annotations

from gateway.auditor.findings import Severity
from gateway.auditor.scanners.repeat_question import RepeatQuestionScanner


def _turn(turn_id: str, user_msg: str, bot: str = "hive") -> dict:
    return {
        "ts": 0,
        "turn_id": turn_id,
        "bot": bot,
        "user_msg": user_msg,
        "user_id": 1,
    }


def test_no_repeats_no_findings() -> None:
    s = RepeatQuestionScanner()
    out = s.scan(
        turns=[
            _turn("t1", "what's the weather?"),
            _turn("t2", "tell me a joke"),
        ],
        memories=[],
    )
    assert out == []


def test_two_identical_questions_emits_low_finding() -> None:
    s = RepeatQuestionScanner()
    out = s.scan(
        turns=[
            _turn("t1", "what did we discuss about kraken?"),
            _turn("t2", "tell me a joke"),
            _turn("t3", "what did we discuss about kraken?"),
        ],
        memories=[],
    )
    assert len(out) == 1
    f = out[0]
    assert f.kind == "repeat_question"
    assert f.severity == Severity.LOW
    assert "kraken" in f.summary.lower()
    assert "t1" in f.detail and "t3" in f.detail


def test_three_or_more_repeats_escalates_to_medium() -> None:
    s = RepeatQuestionScanner()
    msg = "what's my favorite color?"
    out = s.scan(
        turns=[_turn(f"t{i}", msg) for i in range(4)],
        memories=[],
    )
    assert len(out) == 1
    assert out[0].severity == Severity.MEDIUM


def test_normalizes_whitespace_and_case() -> None:
    s = RepeatQuestionScanner()
    out = s.scan(
        turns=[
            _turn("t1", "What's My Favorite Color?"),
            _turn("t2", "  what's my favorite color?  "),
        ],
        memories=[],
    )
    assert len(out) == 1
