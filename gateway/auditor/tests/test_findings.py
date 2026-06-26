# gateway/auditor/tests/test_findings.py
"""Tests for the Finding dataclass + Severity enum."""
from __future__ import annotations

import pytest

from gateway.auditor.findings import Finding, Severity


def test_severity_ordering() -> None:
    assert Severity.LOW < Severity.MEDIUM < Severity.HIGH


def test_finding_construction() -> None:
    f = Finding(
        kind="hallucination",
        severity=Severity.HIGH,
        turn_id="turn_abc",
        bot="hive",
        summary="Claimed Penguin's color is blue; vault says red.",
        detail="people/penguin.md",
    )
    assert f.kind == "hallucination"
    assert f.severity is Severity.HIGH
    assert f.turn_id == "turn_abc"
    assert f.bot == "hive"


def test_finding_kind_must_be_known() -> None:
    with pytest.raises(ValueError, match="kind"):
        Finding(
            kind="unknown_kind",
            severity=Severity.LOW,
            turn_id="t",
            bot="hive",
            summary="x",
            detail="x",
        )


def test_finding_to_markdown_bullet() -> None:
    f = Finding(
        kind="repeat_question",
        severity=Severity.LOW,
        turn_id="t1",
        bot="hive",
        summary="user asked about kraken twice in 10 minutes",
        detail="turns: t1, t9",
    )
    line = f.to_markdown_bullet()
    assert "t1" in line
    assert "kraken" in line
