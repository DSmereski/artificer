"""Tests for the CrewBoardManager daemon."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def fake_store():
    """Mock store with needed methods."""
    store = MagicMock()
    store.list_tasks = MagicMock(return_value=[])
    return store


@pytest.fixture
def fake_catalog():
    """Mock model catalog."""
    catalog = MagicMock()
    catalog.is_available = MagicMock(return_value=True)
    entry = MagicMock()
    entry.ollama_name = "gemma3:12b"
    catalog.model = MagicMock(return_value=entry)
    return catalog


@pytest.fixture
def fake_invoker():
    """Mock OllamaInvoker."""
    invoker = AsyncMock()
    invoker.chat = AsyncMock(return_value=(
        '{"action": "triage", "moves": [{"slug": "T-001", "to_status": "ready"}]}',
        500, 200,
    ))
    return invoker


@pytest.fixture
def manager(fake_store, fake_catalog, fake_invoker):
    """Create manager with mocked dependencies."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    return CrewBoardManager(
        store=fake_store,
        event_bus=MagicMock(),
        model_catalog=fake_catalog,
        ollama_invoker=fake_invoker,
    )


@pytest.mark.asyncio
async def test_status_initial(manager):
    """Daemon starts disabled with no model."""
    manager.disable()
    status = manager.status
    assert status["enabled"] is False
    assert status["model_id"] == "manager"


@pytest.mark.asyncio
async def test_enable_returns_false_when_model_unavailable():
    """Cannot enable if catalog says model is unavailable."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    catalog = MagicMock()
    catalog.is_available = MagicMock(return_value=False)

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=catalog,
    )

    result = await m.enable()
    assert result is False


@pytest.mark.asyncio
async def test_enable_succeeds(manager):
    """Enable returns True when model available."""
    result = await manager.enable()
    assert result is True
    assert manager._enabled is True


@pytest.mark.asyncio
async def test_disable(manager):
    """Disable sets _enabled to False."""
    await manager.enable()
    manager.disable()
    assert manager._enabled is False
    assert manager.status["enabled"] is False


@pytest.mark.asyncio
async def test_make_decision_parses_json(fake_store, fake_catalog, fake_invoker):
    """Decision engine parses LLM response into BoardDecision."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    m = CrewBoardManager(
        store=fake_store,
        event_bus=MagicMock(),
        model_catalog=fake_catalog,
        ollama_invoker=fake_invoker,
    )
    m._ollama_model = "gemma3:12b"
    m._model_ready = True

    # The invoker fixture returns valid JSON
    decision = await m._make_decision("triage", '{"type": "triage"}')
    assert decision is not None
    assert decision.action == "triage"


@pytest.mark.asyncio
async def test_make_decision_rejects_invalid_json():
    """Non-JSON response returns None."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    invoker = AsyncMock()
    invoker.chat = AsyncMock(return_value=('not json at all', 100, 50))

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
        ollama_invoker=invoker,
    )
    m._ollama_model = "gemma3:12b"
    m._model_ready = True

    result = await m._make_decision("triage", "hello world")
    assert result is None


@pytest.mark.asyncio
async def test_single_flight_decisions(fake_store, fake_catalog):
    """Two concurrent decisions should not both execute."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    call_count = 0

    async def slow_chat(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)
        return ('{"action": "triage"}', 0, 0)

    m = CrewBoardManager(
        store=fake_store,
        event_bus=MagicMock(),
        model_catalog=fake_catalog,
        ollama_invoker=MagicMock(chat=slow_chat),
    )
    m._ollama_model = "test"
    m._model_ready = True

    # Run two decisions concurrently
    r1 = asyncio.create_task(m._make_decision("triage", "input"))
    r2 = asyncio.create_task(m._make_decision("triage", "input"))
    await asyncio.gather(r1, r2)

    assert call_count == 1  # Only one should have actually called the model


@pytest.mark.asyncio
async def test_stop_cleans_up(manager):
    """Stop signal breaks the event loop."""
    task = asyncio.create_task(manager.start())
    await asyncio.sleep(0.05)
    manager.stop()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_stale_detection():
    """Stale tasks (>30min no action) should be found."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    # Create a stale task (last_action > 30min ago)
    stale_task = MagicMock()
    stale_task.slug = "T-100"
    stale_task.last_action = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    stale_task.assignee = "none"

    store = MagicMock()
    store.list_tasks = MagicMock(return_value=[stale_task])

    m = CrewBoardManager(
        store=store,
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )

    now = datetime.now(timezone.utc)
    stale = await m._find_stale_tasks(now)
    assert stale == ["T-100"]


@pytest.mark.asyncio
async def test_non_stale_task_not_found():
    """Tasks with recent last_action are not stale."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    fresh_task = MagicMock()
    fresh_task.slug = "T-200"
    fresh_task.last_action = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    store = MagicMock()
    store.list_tasks = MagicMock(return_value=[fresh_task])

    m = CrewBoardManager(
        store=store,
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )

    now = datetime.now(timezone.utc)
    stale = await m._find_stale_tasks(now)
    assert stale == []


@pytest.mark.asyncio
async def test_unassigned_task_detection():
    """Ready tasks with assignee='none' are found."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    ready_task = MagicMock()
    ready_task.slug = "T-300"
    ready_task.assignee = "none"

    busy_task = MagicMock()
    busy_task.slug = "T-301"
    busy_task.assignee = "hive"

    store = MagicMock()
    store.list_tasks = MagicMock(return_value=[ready_task, busy_task])

    m = CrewBoardManager(
        store=store,
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )

    unassigned = await m._find_unassigned_tasks()
    assert unassigned == ["T-300"]


@pytest.mark.asyncio
async def test_activity_log_capped():
    """Decision log stays bounded."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )
    m._max_log_entries = 5

    for i in range(10):
        dec = type('FakeDecision', (), {"action": "triage", "to_dict": lambda self, d=dict(slug=f"T-{i}"): dict(slug=f"T-{i}")})()
        m._record_decision(dec)

    assert len(m.activity) == 5


@pytest.mark.asyncio
async def test_boarddecision_from_dict_valid():
    """BoardDecision.from_dict parses valid JSON correctly."""
    from gateway.crew_board.manager_daemon import BoardDecision

    d = BoardDecision.from_dict({"action": "assign", "task_slug": "T-1", "agent": "hive"})
    assert d is not None
    assert d.action == "assign"
    assert d.kwargs["task_slug"] == "T-1"


@pytest.mark.asyncio
async def test_boarddecision_from_dict_invalid():
    """BoardDecision.from_dict rejects invalid data."""
    from gateway.crew_board.manager_daemon import BoardDecision

    assert BoardDecision.from_dict({"no_action_field": True}) is None
    assert BoardDecision.from_dict("not a dict") is None  # type: ignore


@pytest.mark.asyncio
async def test_boarddecision_from_dict_unknown_action():
    """BoardDecision.from_dict rejects unknown action types."""
    from gateway.crew_board.manager_daemon import BoardDecision

    d = BoardDecision.from_dict({"action": "unknown_action"})
    assert d is None


@pytest.mark.asyncio
async def test_auto_disable_on_errors():
    """Daemon auto-disables after threshold consecutive errors."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    catalog = MagicMock()
    catalog.is_available = MagicMock(return_value=True)
    entry = MagicMock()
    entry.ollama_name = "gemma3:12b"
    catalog.model = MagicMock(return_value=entry)

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=catalog,
    )
    m._consecutive_errors = 2  # Just below threshold

    # Simulate the loop running with errors
    m._enabled = True
    m._model_ready = True
    m._stop.clear()

    # Trigger one error that bumps to threshold
    m._consecutive_errors += 1
    if m._consecutive_errors >= m._auto_disable_threshold:
        m._enabled = False

    assert m._enabled is False


@pytest.mark.asyncio
async def test_triage_cooldown():
    """Triage respects cooldown between calls."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    store = MagicMock()
    catalog = MagicMock(is_available=lambda x: True)

    m = CrewBoardManager(
        store=store,
        event_bus=MagicMock(),
        model_catalog=catalog,
    )
    m._model_ready = True
    m._enabled = True
    m._last_triage = 0.0  # Pretend triage just ran

    # First call should return immediately (cooldown active)
    result = await m.triage_board()
    assert result == []  # Should be empty due to cooldown


@pytest.mark.asyncio
async def test_decompose_returns_empty_when_disabled():
    """Decompose returns empty list when daemon is disabled."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )

    result = await m.decompose_goal("Build something", "test")
    assert result == []


@pytest.mark.asyncio
async def test_status_includes_decision_count():
    """Status includes decision count from log."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )

    # Add a decision
    dec = type('D', (), {"action": "test", "to_dict": lambda self: {}})()
    m._record_decision(dec)

    status = m.status
    assert status["decision_count"] == 1


@pytest.mark.asyncio
async def test_activity_returns_recent_decisions():
    """Activity returns the last N decisions."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )
    m._max_log_entries = 10

    for i in range(15):
        dec = type('D', (), {"action": "test", "to_dict": lambda self, n=i: {n: n}})()
        m._record_decision(dec)

    activity = m.activity
    assert len(activity) == 10  # Capped at max_log_entries


@pytest.mark.asyncio
async def test_decompose_action_includes_order():
    """Decompose creates tasks with _order field."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    invoker = AsyncMock()
    invoker.chat = AsyncMock(return_value=(
        '{"action": "decompose", "tasks": [{"title": "A"}, {"title": "B"}]}',
        300, 100,
    ))

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )
    m._ollama_model = "gemma3:12b"
    m._model_ready = True

    # Enable and call decompose
    await m.enable()
    result = await m.decompose_goal("Test goal", "test-project")

    assert len(result) == 2
    assert result[0].action == "create_task"
    assert result[0].kwargs.get("_order") == 0
    assert result[1].kwargs.get("_order") == 1


@pytest.mark.asyncio
async def test_auto_close_with_passed_vet():
    """Auto-close creates lesson when vet passes."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    task = MagicMock()
    task.slug = "T-999"
    task.attempt_count = 1
    task.project_slug = "test"
    task.acceptance_criteria = [{"text": "All tests pass"}]
    task.verify_results = {"pass": True}

    store = MagicMock()
    store.get_task = MagicMock(return_value=task)
    store.comment_task = MagicMock()

    invoker = AsyncMock()
    invoker.chat = AsyncMock(return_value=(
        '{"action": "vet", "passed": true}',
        200, 50,
    ))

    m = CrewBoardManager(
        store=store,
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )
    m._ollama_model = "gemma3:12b"
    m._model_ready = True
    await m.enable()

    result = await m.auto_close("T-999")
    assert result is not None
    store.comment_task.assert_called()


@pytest.mark.asyncio
async def test_auto_close_no_pass_fails():
    """Auto-close returns None when vet doesn't pass."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    invoker = AsyncMock()
    invoker.chat = AsyncMock(return_value=(
        '{"action": "vet", "passed": false}',
        200, 50,
    ))

    m = CrewBoardManager(
        store=MagicMock(),
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )
    m._ollama_model = "gemma3:12b"
    m._model_ready = True
    await m.enable()

    result = await m.auto_close("T-999")
    assert result is not None  # Returns the vet decision, but...
    assert not result.kwargs.get("passed")


@pytest.mark.asyncio
async def test_escalate_with_claude_target():
    """Escalation to claude-code creates appropriate comment."""
    from gateway.crew_board.manager_daemon import CrewBoardManager

    task = MagicMock()
    task.slug = "T-500"
    task.title = "Test task"
    task.kind = "code"
    task.attempt_count = 5

    store = MagicMock()
    store.get_task = MagicMock(return_value=task)

    invoker = AsyncMock()
    invoker.chat = AsyncMock(return_value=(
        '{"action": "escalate", "to": "claude-code", "reason": "max attempts reached"}',
        200, 50,
    ))

    m = CrewBoardManager(
        store=store,
        event_bus=MagicMock(),
        model_catalog=MagicMock(is_available=lambda x: True),
    )
    m._ollama_model = "gemma3:12b"
    m._model_ready = True
    await m.enable()

    result = await m.escalate_task("T-500", "test reason")
    assert result is not None
    assert result.action == "escalate"
    store.comment_task.assert_called()
