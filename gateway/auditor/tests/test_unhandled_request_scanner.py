# gateway/auditor/tests/test_unhandled_request_scanner.py
"""Tests for the unhandled-request scanner."""
from __future__ import annotations

from gateway.auditor.findings import Severity
from gateway.auditor.scanners.unhandled_request import UnhandledRequestScanner


def _turn(*, turn_id: str, user_msg: str, actions: list[dict]) -> dict:
    return {
        "ts": 0,
        "turn_id": turn_id,
        "bot": "hive",
        "user_msg": user_msg,
        "user_id": 1,
        "synthesis": {"actions": actions},
    }


def test_image_request_with_image_action_no_finding() -> None:
    s = UnhandledRequestScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t1",
            user_msg="make me a picture of a cat",
            actions=[{"verb": "image_render"}],
        )],
        memories=[],
    )
    assert out == []


def test_image_request_without_action_emits_finding() -> None:
    s = UnhandledRequestScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t1",
            user_msg="make me a picture of a cat",
            actions=[],
        )],
        memories=[],
    )
    assert len(out) == 1
    assert out[0].kind == "unhandled_request"
    assert out[0].severity == Severity.MEDIUM


def test_remember_request_without_vault_learn_emits_finding() -> None:
    s = UnhandledRequestScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t9",
            user_msg="remember that my favorite color is red",
            actions=[],
        )],
        memories=[],
    )
    assert len(out) == 1
    assert "vault" in out[0].detail or "remember" in out[0].summary.lower()


def test_unrelated_chat_no_finding() -> None:
    s = UnhandledRequestScanner()
    out = s.scan(
        turns=[_turn(
            turn_id="t2",
            user_msg="what's the weather like?",
            actions=[],
        )],
        memories=[],
    )
    assert out == []
