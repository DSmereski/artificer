"""Threshold + write behaviour for the auditor suggestions writer.

The writer must:
  - emit nothing when no kind crosses its threshold (steady state)
  - emit one note covering each kind that did cross (compose pass)
  - persist via VaultClient.learn with the review-only tag
  - keep going (return None) when the vault write itself fails
"""
from __future__ import annotations

import pytest

from gateway.auditor.findings import Finding, Severity
from gateway.auditor.suggestions_writer import (
    compose_suggestion_sections,
    write_suggestions,
)


def _fnd(kind: str, n: int) -> list[Finding]:
    return [
        Finding(
            kind=kind, severity=Severity.MEDIUM,
            turn_id=f"t-{i}", bot="terry", summary="x",
        )
        for i in range(n)
    ]


class _FakeVault:
    def __init__(self, *, raise_exc: bool = False) -> None:
        self.calls: list[dict] = []
        self._raise = raise_exc

    async def learn(self, **kwargs):
        if self._raise:
            raise RuntimeError("daemon down")
        self.calls.append(kwargs)
        return {"ok": True}


def test_under_threshold_emits_nothing():
    # Hallucination threshold is 3; 2 must not fire.
    assert compose_suggestion_sections(_fnd("hallucination", 2)) == []


def test_at_threshold_emits_one_section():
    sections = compose_suggestion_sections(_fnd("hallucination", 3))
    assert len(sections) == 1
    assert "hallucination" in sections[0]
    assert "bench_harness" in sections[0]


def test_multiple_kinds_each_get_a_section():
    findings = _fnd("hallucination", 3) + _fnd("skill_gap", 2)
    sections = compose_suggestion_sections(findings)
    assert len(sections) == 2
    joined = "\n".join(sections)
    assert "hallucination" in joined
    assert "skill_gap" in joined


def test_security_threshold_is_one():
    sections = compose_suggestion_sections(_fnd("security", 1))
    assert len(sections) == 1


@pytest.mark.asyncio
async def test_write_persists_when_threshold_crossed():
    vault = _FakeVault()
    body = await write_suggestions(
        vault=vault,
        window_label="2026-05-29T10",
        findings=_fnd("hallucination", 3),
    )
    assert body is not None
    assert len(vault.calls) == 1
    call = vault.calls[0]
    assert call["category"] == "ops/auditor-suggestions"
    assert "review-only" in call["tags"]
    assert "Review-only" in call["body"]
    assert "2026-05-29T10" in call["title"]


@pytest.mark.asyncio
async def test_write_skips_when_quiet():
    vault = _FakeVault()
    body = await write_suggestions(
        vault=vault,
        window_label="2026-05-29T10",
        findings=_fnd("hallucination", 1),  # below threshold
    )
    assert body is None
    assert vault.calls == []


@pytest.mark.asyncio
async def test_vault_failure_does_not_raise():
    vault = _FakeVault(raise_exc=True)
    # Must not propagate the RuntimeError — suggestions are best-effort.
    body = await write_suggestions(
        vault=vault,
        window_label="x",
        findings=_fnd("hallucination", 3),
    )
    assert body is not None  # still returns the composed body
