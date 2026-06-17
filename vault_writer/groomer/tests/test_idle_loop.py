# vault_writer/groomer/tests/test_idle_loop.py
"""Tests for the idle-detection groomer loop."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from vault_writer.groomer.idle_loop import IdleGroomerLoop


class _Dispatcher:
    def __init__(self, has_active: bool = False) -> None:
        self.has_active = has_active

    def list_recent(self, status: str | None = None, limit: int = 1):
        return [{"id": 1}] if self.has_active else []


class _AppState:
    def __init__(self, last_turn_at: float = 0.0,
                 dispatcher: Any = None) -> None:
        self.last_turn_completed_at = last_turn_at
        self.dispatcher = dispatcher


@pytest.mark.asyncio
async def test_is_idle_when_no_jobs_and_quiet(tmp_path: Path) -> None:
    st = _AppState(last_turn_at=time.time() - 600, dispatcher=_Dispatcher(False))
    loop = IdleGroomerLoop(vault_path=tmp_path, app_state=st)
    assert loop.is_idle(now_ts=time.time()) is True


@pytest.mark.asyncio
async def test_not_idle_when_jobs_active(tmp_path: Path) -> None:
    st = _AppState(last_turn_at=time.time() - 600, dispatcher=_Dispatcher(True))
    loop = IdleGroomerLoop(vault_path=tmp_path, app_state=st)
    assert loop.is_idle(now_ts=time.time()) is False


@pytest.mark.asyncio
async def test_not_idle_when_recent_turn(tmp_path: Path) -> None:
    st = _AppState(last_turn_at=time.time() - 30, dispatcher=_Dispatcher(False))
    loop = IdleGroomerLoop(vault_path=tmp_path, app_state=st)
    assert loop.is_idle(now_ts=time.time()) is False


@pytest.mark.asyncio
async def test_tick_runs_scanner_when_confirmed_idle(tmp_path: Path) -> None:
    calls: list[int] = []

    async def fake_run(**kwargs: Any) -> dict[str, int]:
        calls.append(1)
        return {"dup_scanner": 0}

    st = _AppState(last_turn_at=0.0, dispatcher=_Dispatcher(False))
    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st, run_fn=fake_run,
        idle_confirmation_ticks=2,
    )
    # Two idle ticks confirm; third tick should run.
    await loop.tick(now_ts=time.time())
    await loop.tick(now_ts=time.time())
    await loop.tick(now_ts=time.time())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tick_resets_on_non_idle(tmp_path: Path) -> None:
    calls: list[int] = []

    async def fake_run(**kwargs: Any) -> dict[str, int]:
        calls.append(1)
        return {}

    dispatcher = _Dispatcher(False)
    st = _AppState(last_turn_at=0.0, dispatcher=dispatcher)
    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st, run_fn=fake_run,
        idle_confirmation_ticks=3,
    )
    await loop.tick(now_ts=time.time())
    # Become busy.
    dispatcher.has_active = True
    await loop.tick(now_ts=time.time())
    # Idle again — counter must have reset.
    dispatcher.has_active = False
    await loop.tick(now_ts=time.time())
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_tick_passes_apply_auto_true(tmp_path: Path) -> None:
    """Idle-path runs must pass apply_auto=True so auto-fixes are applied."""
    captured_kwargs: list[dict] = []

    async def fake_run(**kwargs: Any) -> dict[str, int]:
        captured_kwargs.append(dict(kwargs))
        return {}

    st = _AppState(last_turn_at=0.0, dispatcher=_Dispatcher(False))
    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st, run_fn=fake_run,
        idle_confirmation_ticks=1,
    )
    await loop.tick(now_ts=time.time())
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("apply_auto") is True


class _DispatcherWithStatusMix:
    """Dispatcher stub that tracks per-status counts so the queued-vs-
    dispatched check can be exercised independently."""
    def __init__(self, queued: int = 0, dispatched: int = 0) -> None:
        self.queued = queued
        self.dispatched = dispatched

    def has_active(self) -> bool:
        return self.queued > 0 or self.dispatched > 0


@pytest.mark.asyncio
async def test_not_idle_when_jobs_queued_only(tmp_path: Path) -> None:
    """Spec line 158: queued jobs must block idle-path runs too,
    not only dispatched ones. Otherwise the groomer can fire while
    the dispatcher is about to assign a slow GPU job."""
    dispatcher = _DispatcherWithStatusMix(queued=1)
    st = _AppState(last_turn_at=time.time() - 600, dispatcher=dispatcher)
    loop = IdleGroomerLoop(vault_path=tmp_path, app_state=st)
    assert loop.is_idle(now_ts=time.time()) is False


@pytest.mark.asyncio
async def test_not_idle_when_chat_session_open(tmp_path: Path) -> None:
    """Spec line 158: an open WS chat session must block the groomer.
    Even if the dispatcher is empty, the user is mid-conversation."""
    dispatcher = _DispatcherWithStatusMix()
    st = _AppState(last_turn_at=time.time() - 600, dispatcher=dispatcher)
    st.chat_sessions = {"sock-1": object()}
    loop = IdleGroomerLoop(vault_path=tmp_path, app_state=st)
    assert loop.is_idle(now_ts=time.time()) is False


@pytest.mark.asyncio
async def test_dispatcher_error_treated_as_not_idle(tmp_path: Path) -> None:
    """When the dispatcher raises, fail safe: assume jobs MAY be
    pending and skip the groomer run rather than hammer the vault
    while a partial outage is in progress."""
    class _ErrDispatcher:
        def has_active(self) -> bool:
            raise RuntimeError("db locked")

        def list_recent(self, **k):
            raise RuntimeError("db locked")
    st = _AppState(last_turn_at=time.time() - 600, dispatcher=_ErrDispatcher())
    loop = IdleGroomerLoop(vault_path=tmp_path, app_state=st)
    assert loop.is_idle(now_ts=time.time()) is False


@pytest.mark.asyncio
async def test_start_stop_cleanly(tmp_path: Path) -> None:
    st = _AppState(last_turn_at=0.0, dispatcher=_Dispatcher(False))
    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st,
        run_fn=lambda **k: asyncio.sleep(0, result={}),  # type: ignore[arg-type]
        tick_interval_s=0.05,
    )
    loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()
    assert loop._task is None


# ---- hive_turn_active gate tests ----


class _AppStateWithEvent(_AppState):
    def __init__(self, last_turn_at: float = 0.0,
                 dispatcher: Any = None) -> None:
        super().__init__(last_turn_at=last_turn_at, dispatcher=dispatcher)
        self.hive_turn_active = asyncio.Event()


@pytest.mark.asyncio
async def test_groomer_skips_run_while_turn_active(tmp_path: Path) -> None:
    """Gate active → groomer must not invoke the run_fn at all."""
    calls: list[int] = []

    async def fake_run(**kwargs: Any) -> dict[str, int]:
        calls.append(1)
        return {}

    st = _AppStateWithEvent(last_turn_at=0.0, dispatcher=_Dispatcher(False))
    st.hive_turn_active.set()

    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st, run_fn=fake_run,
        idle_confirmation_ticks=1,
        # very short wait so the test completes fast, but gate never clears
        turn_poll_s=0.01,
        turn_wait_max_s=0.05,
    )
    await loop.tick(now_ts=time.time())
    assert calls == [], "run_fn must not fire while hive turn is active"


@pytest.mark.asyncio
async def test_groomer_resumes_after_turn_clears(tmp_path: Path) -> None:
    """Gate clears mid-wait → groomer must proceed and invoke run_fn."""
    calls: list[int] = []

    async def fake_run(**kwargs: Any) -> dict[str, int]:
        calls.append(1)
        return {}

    st = _AppStateWithEvent(last_turn_at=0.0, dispatcher=_Dispatcher(False))
    st.hive_turn_active.set()

    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st, run_fn=fake_run,
        idle_confirmation_ticks=1,
        turn_poll_s=0.02,
        turn_wait_max_s=2.0,
    )

    async def _clear_later() -> None:
        await asyncio.sleep(0.04)
        st.hive_turn_active.clear()

    asyncio.create_task(_clear_later())
    await loop.tick(now_ts=time.time())
    assert calls == [1], "run_fn must fire once the turn gate clears"


@pytest.mark.asyncio
async def test_groomer_runs_immediately_when_no_gate(tmp_path: Path) -> None:
    """AppState without hive_turn_active behaves as before (gate absent = safe)."""
    calls: list[int] = []

    async def fake_run(**kwargs: Any) -> dict[str, int]:
        calls.append(1)
        return {}

    st = _AppState(last_turn_at=0.0, dispatcher=_Dispatcher(False))

    loop = IdleGroomerLoop(
        vault_path=tmp_path, app_state=st, run_fn=fake_run,
        idle_confirmation_ticks=1,
    )
    await loop.tick(now_ts=time.time())
    assert calls == [1]
