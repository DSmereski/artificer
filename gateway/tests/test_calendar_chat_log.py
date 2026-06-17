"""Calendar fires for `hive_turn` get indexed into chat_log under the
dedicated `calendar` thread, so the unified search bar can surface them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from gateway.app import _make_calendar_fire
from gateway.calendar_jobs import Job
from gateway.deps import AppState


@dataclass
class _FakeTurn:
    reply: str = "ok"
    blocked: bool = False
    error: str | None = None
    turn_id: str = "t1"


class _FakeCoordinator:
    async def coordinate(self, ctx, emitter):  # noqa: ARG002
        return _FakeTurn()


class _SpyVaultClient:
    def __init__(self):
        self.calls: list[dict] = []

    async def chat_log_append(
        self, *, bot, user_id, role, content,
        thread_id="default", turn_id=None, **_,
    ):
        self.calls.append({
            "bot": bot, "user_id": user_id, "role": role,
            "content": content, "thread_id": thread_id,
            "turn_id": turn_id,
        })
        return {"ok": True}


def _job(user_msg: str = "what's new?") -> Job:
    return Job(
        id="abc",
        title="daily check-in",
        description="",
        scheduled_at="2026-01-01T00:00:00+00:00",
        recurrence="daily",
        action_verb="hive_turn",
        action_payload={"user_msg": user_msg},
    )


@pytest.mark.asyncio
async def test_calendar_hive_fire_indexes_into_chat_log(tmp_path):
    """A successful calendar hive_turn writes both the user msg and
    assistant reply into chat_log with thread_id='calendar'."""
    spy_vc = _SpyVaultClient()

    # Minimal AppState — only the attributes the indexer uses.
    class _MiniState:
        vault_client = spy_vc
        background_tasks: set = set()

    holder: dict = {"state": _MiniState()}

    fire = _make_calendar_fire(
        hive_coordinator=_FakeCoordinator(),
        executor=None,
        app_state_holder=holder,
    )

    result = await fire(_job("hello hive"))
    assert result.ok is True
    # The indexer schedules the appends as background tasks, so wait
    # for them to drain before asserting on the spy.
    pending = list(_MiniState.background_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    threads = {c["thread_id"] for c in spy_vc.calls}
    roles = {c["role"] for c in spy_vc.calls}
    assert threads == {"calendar"}
    assert roles == {"user", "assistant"}
    user_call = next(c for c in spy_vc.calls if c["role"] == "user")
    asst_call = next(c for c in spy_vc.calls if c["role"] == "assistant")
    assert user_call["content"] == "hello hive"
    assert asst_call["content"] == "ok"


@pytest.mark.asyncio
async def test_calendar_fire_without_app_state_still_succeeds():
    """If the app_state holder is empty (rare race during startup) the
    fire still succeeds — chat_log indexing is best-effort."""
    holder: dict = {"state": None}
    fire = _make_calendar_fire(
        hive_coordinator=_FakeCoordinator(),
        executor=None,
        app_state_holder=holder,
    )
    result = await fire(_job())
    assert result.ok is True
