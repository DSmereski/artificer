"""Tests for the calendar (scheduled jobs) backend."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.calendar_jobs import (
    FireResult, Job, JobStore, Scheduler, validate_payload,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- payload validation


def test_validate_payload_unknown_verb():
    assert validate_payload("nuke", {}) == "unknown verb: 'nuke'"


def test_hive_turn_allowed_with_user_msg():
    """`hive_turn` is on the calendar verb whitelist for the single-user
    homelab — 'Run a Hive prompt' was the user's explicit ask. The
    stolen-token threat exists but is bounded by Tailscale + bearer
    auth. Validation still rejects missing or oversized user_msg so
    a typo can't queue a runaway turn."""
    assert validate_payload("hive_turn", {"user_msg": "hi"}) is None
    err = validate_payload("hive_turn", {})
    assert err is not None and "user_msg" in err
    err = validate_payload("hive_turn", {"user_msg": "x" * 3000})
    assert err is not None and "2000" in err


def test_validate_ntfy_push_needs_message():
    assert validate_payload("ntfy_push", {}) is not None
    assert validate_payload("ntfy_push", {"message": "hi"}) is None


def test_validate_vault_learn_needs_all_fields():
    assert validate_payload("vault_learn", {"category": "k"}) is not None
    assert validate_payload("vault_learn", {
        "category": "knowledge", "title": "t", "body": "b",
    }) is None


def test_validate_image_render_needs_prompt():
    assert validate_payload("image_render", {}) is not None
    assert validate_payload("image_render", {"prompt": "an elf"}) is None


def test_image_render_reference_path_forbidden():
    """Scheduled jobs can't take a raw filesystem `reference_path` —
    it would let a stolen token read arbitrary files into a render."""
    err = validate_payload(
        "image_render",
        {"prompt": "x", "reference_path": "/etc/passwd"},
    )
    assert err is not None
    assert "reference_path" in err


# ---------------------------------------------------------------- store


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "calendar.db")


def test_store_create_and_get(store):
    when = _iso(_now() + timedelta(hours=1))
    job = store.create(
        title="ping", scheduled_at=when, recurrence="none",
        action_verb="ntfy_push", action_payload={"message": "hi"},
        owner_device_id="d1",
    )
    assert job.id
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.title == "ping"
    assert fetched.action_payload == {"message": "hi"}
    assert fetched.notify is True


def test_store_create_rejects_bad_payload(store):
    with pytest.raises(ValueError):
        store.create(
            title="x",
            scheduled_at=_iso(_now()),
            action_verb="ntfy_push",
            action_payload={},
        )


def test_store_create_rejects_bad_recurrence(store):
    with pytest.raises(ValueError):
        store.create(
            title="x", scheduled_at=_iso(_now()),
            recurrence="hourly",
            action_verb="ntfy_push", action_payload={"message": "x"},
        )


def test_store_list_window(store):
    base = _now()
    for i, hour in enumerate([1, 5, 100]):
        store.create(
            title=f"j{i}",
            scheduled_at=_iso(base + timedelta(hours=hour)),
            action_verb="ntfy_push",
            action_payload={"message": "x"},
            owner_device_id="d1",
        )
    in_window = store.list(
        owner_device_id="d1",
        since=_iso(base),
        until=_iso(base + timedelta(hours=10)),
    )
    assert len(in_window) == 2


def test_store_due(store):
    past = _iso(_now() - timedelta(minutes=1))
    future = _iso(_now() + timedelta(hours=1))
    store.create(
        title="now", scheduled_at=past,
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    store.create(
        title="later", scheduled_at=future,
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    due = store.due()
    assert len(due) == 1
    assert due[0].title == "now"


def test_store_update(store):
    j = store.create(
        title="x", scheduled_at=_iso(_now()),
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    updated = store.update(j.id, title="renamed")
    assert updated.title == "renamed"
    assert store.get(j.id).title == "renamed"


def test_store_delete(store):
    j = store.create(
        title="x", scheduled_at=_iso(_now()),
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    assert store.delete(j.id) is True
    assert store.get(j.id) is None
    assert store.delete(j.id) is False


def test_owner_filter(store):
    store.create(
        title="a", scheduled_at=_iso(_now()),
        action_verb="ntfy_push", action_payload={"message": "x"},
        owner_device_id="d1",
    )
    store.create(
        title="b", scheduled_at=_iso(_now()),
        action_verb="ntfy_push", action_payload={"message": "x"},
        owner_device_id="d2",
    )
    store.create(
        title="system", scheduled_at=_iso(_now()),
        action_verb="ntfy_push", action_payload={"message": "x"},
        owner_device_id="",   # system-wide
    )
    d1_jobs = {j.title for j in store.list(owner_device_id="d1")}
    assert d1_jobs == {"a", "system"}


# ---------------------------------------------------------------- recurrence


def test_advance_none_marks_done():
    j = Job(
        id="x", title="t", description="",
        scheduled_at=_iso(_now() - timedelta(minutes=1)),
        recurrence="none",
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    nxt = j.advance()
    assert nxt.status == "done"


def test_advance_daily_skips_missed():
    """If a daily job missed 3 days, it should land on the next future day."""
    five_days_ago = _now() - timedelta(days=5)
    j = Job(
        id="x", title="t", description="",
        scheduled_at=_iso(five_days_ago),
        recurrence="daily",
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    nxt = j.advance()
    from gateway.calendar_jobs import _parse_iso
    assert _parse_iso(nxt.scheduled_at) > _now()
    assert nxt.status == "scheduled"


def test_advance_weekly():
    j = Job(
        id="x", title="t", description="",
        scheduled_at=_iso(_now() - timedelta(minutes=1)),
        recurrence="weekly",
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    nxt = j.advance()
    from gateway.calendar_jobs import _parse_iso
    delta = _parse_iso(nxt.scheduled_at) - (_now() - timedelta(minutes=1))
    assert timedelta(days=6) < delta < timedelta(days=8)


# ---------------------------------------------------------------- scheduler


@pytest.mark.asyncio
async def test_scheduler_fires_due_job_and_advances(store):
    j = store.create(
        title="ping", scheduled_at=_iso(_now() - timedelta(seconds=5)),
        recurrence="daily",
        action_verb="ntfy_push", action_payload={"message": "x"},
    )

    fired: list = []
    async def fire(job):
        fired.append(job.id)
        return FireResult(job_id=job.id, ok=True, detail="ok")

    sched = Scheduler(store, fire=fire)
    results = await sched.tick()
    assert len(results) == 1
    assert results[0].ok
    assert fired == [j.id]
    # Job advanced; no longer due.
    assert store.get(j.id).status == "scheduled"
    assert store.get(j.id).last_run_at is not None
    assert len(store.due()) == 0


@pytest.mark.asyncio
async def test_scheduler_marks_non_recurring_done(store):
    j = store.create(
        title="once", scheduled_at=_iso(_now() - timedelta(seconds=1)),
        recurrence="none",
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    async def fire(job):
        return FireResult(job_id=job.id, ok=True)
    sched = Scheduler(store, fire=fire)
    await sched.tick()
    assert store.get(j.id).status == "done"


@pytest.mark.asyncio
async def test_scheduler_records_error_detail(store):
    j = store.create(
        title="boom", scheduled_at=_iso(_now() - timedelta(seconds=1)),
        recurrence="daily",
        action_verb="ntfy_push", action_payload={"message": "x"},
    )
    async def fire(job):
        return FireResult(job_id=job.id, ok=False, detail="kaboom")
    sched = Scheduler(store, fire=fire)
    await sched.tick()
    assert store.get(j.id).last_error == "kaboom"


@pytest.mark.asyncio
async def test_scheduler_calls_ntfy_when_notify(store):
    store.create(
        title="t", scheduled_at=_iso(_now() - timedelta(seconds=1)),
        action_verb="ntfy_push", action_payload={"message": "x"},
        notify=True,
    )
    async def fire(job):
        return FireResult(job_id=job.id, ok=True, detail="done")
    fake_ntfy = MagicMock()
    fake_ntfy.enabled = True
    fake_ntfy.publish = AsyncMock()
    sched = Scheduler(store, fire=fire, ntfy=fake_ntfy)
    await sched.tick()
    fake_ntfy.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_skips_ntfy_when_disabled(store):
    store.create(
        title="t", scheduled_at=_iso(_now() - timedelta(seconds=1)),
        action_verb="ntfy_push", action_payload={"message": "x"},
        notify=False,
    )
    async def fire(job):
        return FireResult(job_id=job.id, ok=True)
    fake_ntfy = MagicMock()
    fake_ntfy.enabled = True
    fake_ntfy.publish = AsyncMock()
    sched = Scheduler(store, fire=fire, ntfy=fake_ntfy)
    await sched.tick()
    fake_ntfy.publish.assert_not_called()
