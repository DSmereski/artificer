# gateway/auditor/tests/test_findings_writer.py
"""Tests for the findings writer — composes audit summary + escalations."""
from __future__ import annotations

from typing import Any

import pytest

from gateway.auditor.findings import Finding, Severity
from gateway.auditor.findings_writer import write_audit


def _f(kind: str, sev: Severity, turn_id: str = "t1", summary: str = "x") -> Finding:
    return Finding(
        kind=kind, severity=sev, turn_id=turn_id, bot="terry",
        summary=summary, detail="d",
    )


@pytest.mark.asyncio
async def test_writes_per_hour_summary_only_when_no_high() -> None:
    vault = _FakeVault()
    findings = [
        _f("repeat_question", Severity.LOW),
        _f("skill_gap", Severity.LOW),
    ]
    await write_audit(
        vault=vault,
        window_label="2026-05-01-14",
        turns_scanned=10,
        findings=findings,
    )
    learn_calls = vault.learn_calls
    # One call: per-hour audit.
    assert len(learn_calls) == 1
    assert learn_calls[0]["category"] == "ops/audits"
    assert learn_calls[0]["title"] == "2026-05-01-14"
    body = learn_calls[0]["body"]
    assert "Turns scanned: 10" in body
    assert "Repeat questions: 1" in body
    assert "Skill gaps: 1" in body


@pytest.mark.asyncio
async def test_high_findings_also_write_escalations() -> None:
    vault = _FakeVault()
    findings = [
        _f("hallucination", Severity.HIGH, turn_id="t_bad",
           summary="reply contradicts canon"),
    ]
    await write_audit(
        vault=vault,
        window_label="2026-05-01-14",
        turns_scanned=5,
        findings=findings,
    )
    cats = sorted(c["category"] for c in vault.learn_calls)
    assert cats == ["ops/audits", "ops/escalations"]


@pytest.mark.asyncio
async def test_no_findings_writes_summary_with_zero_counts() -> None:
    vault = _FakeVault()
    await write_audit(
        vault=vault, window_label="2026-05-01-15",
        turns_scanned=2, findings=[],
    )
    assert len(vault.learn_calls) == 1
    body = vault.learn_calls[0]["body"]
    assert "Turns scanned: 2" in body
    assert "Hallucinations: 0" in body


class _FakeVault:
    def __init__(self) -> None:
        self.learn_calls: list[dict[str, Any]] = []

    async def learn(self, **kwargs) -> dict | None:
        self.learn_calls.append(kwargs)
        return {"ok": True}
