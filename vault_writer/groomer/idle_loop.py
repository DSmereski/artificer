# vault_writer/groomer/idle_loop.py
"""Idle-detection background task that drives the groomer.

Wakes every `tick_interval_s` (default 60s). On each tick:
1. Check `is_idle` — no GPU jobs dispatched, last user turn > 5 min ago.
2. If idle, increment a counter. After `idle_confirmation_ticks`
   consecutive idle ticks, run ONE grooming pass via `run_groom`.
3. After a run, reset the counter so we wait again before the next.

Idle-path runs are executed with `apply_auto=True`, so trivially-fixable
issues (trailing whitespace, heading normalisation, frontmatter ordering)
are applied automatically without requiring user review.

This rate-limits the groomer to ~once per 5 idle minutes, which is
slow enough that it can't compete with the user for I/O. Heavier
work (embedding-based dup_scanner over a 1000-note vault) is bounded
by the per-scan suggestion cap.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol


log = logging.getLogger("vault_writer.groomer.idle_loop")


class _AppStateLike(Protocol):
    last_turn_completed_at: float
    dispatcher: Any


RunFn = Callable[..., Awaitable[dict]]

# Max seconds to wait for an active turn to clear before skipping the run.
_TURN_WAIT_MAX_S: float = 30.0
# Poll interval while waiting for a turn to clear.
_TURN_POLL_S: float = 2.0


class IdleGroomerLoop:
    def __init__(
        self,
        *,
        vault_path: Path,
        app_state: _AppStateLike,
        run_fn: RunFn | None = None,
        tick_interval_s: float = 60.0,
        idle_confirmation_ticks: int = 5,
        idle_threshold_s: float = 300.0,
        turn_poll_s: float = _TURN_POLL_S,
        turn_wait_max_s: float = _TURN_WAIT_MAX_S,
    ) -> None:
        self._vault_path = vault_path
        self._app_state = app_state
        self._tick_interval_s = float(tick_interval_s)
        self._idle_confirmation_ticks = int(idle_confirmation_ticks)
        self._idle_threshold_s = float(idle_threshold_s)
        self._turn_poll_s = float(turn_poll_s)
        self._turn_wait_max_s = float(turn_wait_max_s)
        if run_fn is None:
            from vault_writer.groomer.groom_run import run_groom as _default
            run_fn = _default
        self._run_fn = run_fn
        self._task: asyncio.Task | None = None
        self._idle_streak: int = 0

    def is_idle(self, *, now_ts: float | None = None) -> bool:
        ts = now_ts if now_ts is not None else time.time()
        last_turn = float(getattr(self._app_state, "last_turn_completed_at", 0.0) or 0.0)
        if (ts - last_turn) < self._idle_threshold_s:
            return False
        # Open WS chat session ⇒ user is mid-conversation; never groom.
        sessions = getattr(self._app_state, "chat_sessions", None)
        if sessions:
            return False
        dispatcher = getattr(self._app_state, "dispatcher", None)
        if dispatcher is None:
            return True
        # Prefer the explicit boolean helper (covers queued AND
        # dispatched in one SQL hit). Fall back to list_recent for
        # legacy stubs / older dispatchers without has_active.
        try:
            checker = getattr(dispatcher, "has_active", None)
            if callable(checker):
                if checker():
                    return False
            else:
                for status in ("queued", "dispatched"):
                    if dispatcher.list_recent(status=status, limit=1):
                        return False
        except Exception:  # noqa: BLE001
            # Fail SAFE: a dispatcher that's erroring may have
            # pending jobs we just can't see right now. Skip the
            # groom run rather than hammer the vault during an outage.
            log.exception("dispatcher active-check crashed; treating as not-idle")
            return False
        return True

    async def _wait_for_turn_clear(self) -> bool:
        """Poll until the hive turn gate clears or the max wait expires.

        Returns True if safe to proceed, False if we timed out.
        Timeout prevents starvation if a crash leaves the event set forever."""
        turn_event = getattr(self._app_state, "hive_turn_active", None)
        if turn_event is None or not turn_event.is_set():
            return True
        log.debug("groomer: hive turn active — waiting up to %.0fs", self._turn_wait_max_s)
        deadline = time.time() + self._turn_wait_max_s
        while turn_event.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                log.warning(
                    "groomer: hive_turn_active still set after %.0fs — skipping run",
                    self._turn_wait_max_s,
                )
                return False
            await asyncio.sleep(min(self._turn_poll_s, remaining))
        return True

    async def tick(self, *, now_ts: float | None = None) -> None:
        if not self.is_idle(now_ts=now_ts):
            self._idle_streak = 0
            return
        self._idle_streak += 1
        if self._idle_streak < self._idle_confirmation_ticks:
            return
        self._idle_streak = 0  # reset after run
        if not await self._wait_for_turn_clear():
            return
        try:
            await self._run_fn(vault_path=self._vault_path, apply_auto=True)
        except Exception:  # noqa: BLE001
            log.exception("groomer run failed")

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._loop(), name="groomer-idle-loop")
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
            log.exception("groomer idle loop stop raised")
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                log.exception("groomer tick crashed")
            try:
                await asyncio.sleep(self._tick_interval_s)
            except asyncio.CancelledError:
                return
