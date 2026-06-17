# gateway/auditor/scheduler.py
"""Hourly auditor scheduler — single dedicated background task.

Wakes every ``tick_interval_s`` seconds. When the current clock hour
differs from the last hour we ran for, executes one audit pass over
the previous hour's window.

We don't rely on the calendar JobStore because (a) auditor.run is an
internal recurring task, not a user-mutable scheduled job, and (b)
the existing JobStore action-verb allowlist deliberately excludes
anything whose interface lets a stolen device token influence the
audit pipeline.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol


log = logging.getLogger("gateway.auditor.scheduler")


class _VaultLike(Protocol):
    async def learn(self, **kwargs: Any) -> dict | None: ...


RunFn = Callable[..., Awaitable[list]]


class AuditorScheduler:
    def __init__(
        self,
        *,
        state_dir: Path,
        vault: _VaultLike,
        bots: list[str],
        run_fn: RunFn | None = None,
        tick_interval_s: float = 60.0,
    ) -> None:
        self._state_dir = state_dir
        self._vault = vault
        self._bots = list(bots)
        self._tick_interval_s = float(tick_interval_s)
        if run_fn is None:
            from gateway.auditor.audit_run import run_audit as _default
            run_fn = _default
        self._run_fn = run_fn
        self._task: asyncio.Task | None = None
        self._last_run_hour: int = -1

    @staticmethod
    def _current_hour(ts: float) -> int:
        # Hours since epoch, UTC. Stable identifier per clock-hour.
        return int(ts // 3600)

    @staticmethod
    def _label_for(prev_hour: int) -> str:
        # prev_hour is hours-since-epoch; convert to Y-M-D-HH UTC.
        ts = prev_hour * 3600
        return time.strftime("%Y-%m-%d-%H", time.gmtime(ts))

    async def tick(self, *, now_ts: float | None = None) -> None:
        ts = now_ts if now_ts is not None else time.time()
        cur_hour = self._current_hour(ts)
        if cur_hour <= self._last_run_hour:
            return
        prev_hour = cur_hour - 1
        window_start = prev_hour * 3600
        window_end = cur_hour * 3600
        try:
            await self._run_fn(
                state_dir=self._state_dir,
                vault=self._vault,
                bots=self._bots,
                window_start=float(window_start),
                window_end=float(window_end),
                window_label=self._label_for(prev_hour),
            )
        except Exception:  # noqa: BLE001
            log.exception("auditor run failed for hour %s", prev_hour)
        finally:
            # Even on failure, advance so we don't replay the same window.
            self._last_run_hour = cur_hour

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._loop(), name="auditor-scheduler")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("auditor scheduler stop raised")
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                log.exception("auditor tick crashed")
            try:
                await asyncio.sleep(self._tick_interval_s)
            except asyncio.CancelledError:
                return
