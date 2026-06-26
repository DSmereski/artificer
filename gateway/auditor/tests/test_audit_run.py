# gateway/auditor/tests/test_audit_run.py
"""Tests for the auditor's run orchestration."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from gateway.auditor.audit_run import run_audit


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_run_audit_loads_inputs_and_writes_summary(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    log_dir = state_dir / "turn-logs"
    mem_dir = state_dir / "memory" / "hive" / "1"
    mem_dir.mkdir(parents=True)
    (mem_dir / "default.memory.json").write_text(
        json.dumps({"user_facts": [], "decisions": [], "mid_summary": ""}),
        encoding="utf-8",
    )
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    _write_jsonl(log_dir / f"{day}.jsonl", [
        {"ts": now - 1800, "turn_id": "t1", "bot": "hive", "user_id": 1,
         "user_msg": "what's the weather?", "synthesis": {"actions": []},
         "delegations": ["chat_recall"]},
        {"ts": now - 1500, "turn_id": "t2", "bot": "hive", "user_id": 1,
         "user_msg": "what's the weather?", "synthesis": {"actions": []},
         "delegations": ["chat_recall"]},
    ])

    fake = _FakeVault()
    await run_audit(
        state_dir=state_dir,
        vault=fake,
        bots=["hive"],
        window_start=now - 3600,
        window_end=now,
        window_label="2026-05-01-14",
    )
    assert len(fake.learn_calls) >= 1
    summary = next(c for c in fake.learn_calls if c["category"] == "ops/audits")
    assert "Turns scanned: 2" in summary["body"]
    # Two identical user_msgs ⇒ repeat_question finding.
    assert "Repeat questions: 1" in summary["body"]


@pytest.mark.asyncio
async def test_run_audit_no_turns_still_writes_zero_summary(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    fake = _FakeVault()
    await run_audit(
        state_dir=state_dir,
        vault=fake,
        bots=["hive"],
        window_start=0.0,
        window_end=1.0,
        window_label="never",
    )
    assert len(fake.learn_calls) == 1
    assert "Turns scanned: 0" in fake.learn_calls[0]["body"]


class _FakeVault:
    def __init__(self) -> None:
        self.learn_calls: list[dict[str, Any]] = []

    async def learn(self, **kwargs) -> dict | None:
        self.learn_calls.append(kwargs)
        return {"ok": True}
