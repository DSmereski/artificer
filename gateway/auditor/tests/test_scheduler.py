# gateway/auditor/tests/test_scheduler.py
"""Tests for the auditor's hourly scheduler."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gateway.auditor.scheduler import AuditorScheduler


@pytest.mark.asyncio
async def test_tick_runs_audit_when_new_hour(tmp_path: Path) -> None:
    calls: list[dict] = []

    async def fake_run(**kwargs: Any) -> list:
        calls.append(kwargs)
        return []

    sch = AuditorScheduler(
        state_dir=tmp_path,
        vault=_FakeVault(),
        bots=["terry"],
        run_fn=fake_run,
    )
    # Force last_run_hour earlier than current — should trigger.
    sch._last_run_hour = -1
    await sch.tick(now_ts=1714564800.0)  # 2024-05-01 12:00:00 UTC
    assert len(calls) == 1
    label = calls[0]["window_label"]
    # Audit covers [prev_hour, cur_hour) = [11:00, 12:00) UTC, so the
    # label is the START hour: "2024-05-01-11".
    assert label == "2024-05-01-11"
    # And the window the run actually saw matches that label.
    assert calls[0]["window_start"] == 1714561200.0  # 2024-05-01 11:00:00 UTC
    assert calls[0]["window_end"] == 1714564800.0   # 2024-05-01 12:00:00 UTC


@pytest.mark.asyncio
async def test_tick_no_op_in_same_hour(tmp_path: Path) -> None:
    calls: list[dict] = []

    async def fake_run(**kwargs: Any) -> list:
        calls.append(kwargs)
        return []

    sch = AuditorScheduler(
        state_dir=tmp_path,
        vault=_FakeVault(),
        bots=["terry"],
        run_fn=fake_run,
    )
    await sch.tick(now_ts=1714564800.0)
    await sch.tick(now_ts=1714564800.0 + 60)  # same hour
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_start_stop_cleanly(tmp_path: Path) -> None:
    sch = AuditorScheduler(
        state_dir=tmp_path,
        vault=_FakeVault(),
        bots=["terry"],
        run_fn=AsyncNoop(),
        tick_interval_s=0.05,
    )
    sch.start()
    await asyncio.sleep(0.15)
    await sch.stop()
    assert sch._task is None


class _FakeVault:
    async def learn(self, **kwargs: Any) -> dict | None:
        return {"ok": True}


class AsyncNoop:
    async def __call__(self, **kwargs: Any) -> list:
        return []
