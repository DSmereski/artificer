"""Tests for connected-brain Item 1: vault snippet injection into TurnContext.

A turn whose message matches a vault note should receive that note's
snippet in the planner context. The vault search is mocked so tests are
deterministic and offline.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.hive_turn_helpers import (
    _VAULT_SNIPPET_K,
    _VAULT_SNIPPET_MAX_CHARS,
    _fetch_vault_snippets,
    build_turn_context,
    build_turn_context_async,
)
from gateway.hive_coordinator import TurnContext


# ---------------------------------------------------------------- fakes


def _fake_app_state(*, vault_client=None, config=None):
    state = SimpleNamespace()
    state.vault_client = vault_client
    state.config = config
    state.image_build_store = None
    state.skill_registry = None
    state.memory_store_terry = None
    state.helpers = {}
    return state


def _fake_config():
    cfg = SimpleNamespace()
    cfg.vault_writer = SimpleNamespace(embed_model="nomic-embed-text")
    return cfg


def _fake_search_result(path: str, body: str):
    r = SimpleNamespace()
    r.path = path
    r.body = body
    return r


# ---------------------------------------------------------------- TurnContext field


def test_turn_context_has_vault_snippets_field():
    """TurnContext must expose vault_snippets with an empty default."""
    ctx = TurnContext(
        user_msg="hello",
        user_id=1,
        device_id="dev1",
    )
    assert hasattr(ctx, "vault_snippets")
    assert ctx.vault_snippets == []


def test_turn_context_accepts_vault_snippets():
    ctx = TurnContext(
        user_msg="hello",
        user_id=1,
        device_id="dev1",
        vault_snippets=["snippet A", "snippet B"],
    )
    assert ctx.vault_snippets == ["snippet A", "snippet B"]


# ---------------------------------------------------------------- _fetch_vault_snippets


@pytest.mark.asyncio
async def test_fetch_vault_snippets_returns_empty_when_no_vault_client():
    state = _fake_app_state(vault_client=None, config=_fake_config())
    result = await _fetch_vault_snippets(state, text="test query", device_audience=None)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_vault_snippets_returns_empty_when_no_config():
    state = _fake_app_state(vault_client=MagicMock(), config=None)
    result = await _fetch_vault_snippets(state, text="test query", device_audience=None)
    assert result == []


@pytest.mark.asyncio
async def test_fetch_vault_snippets_returns_snippets_on_hit():
    """When vault search returns hits, snippets are formatted and returned."""
    fake_client = MagicMock()
    fake_client.search.return_value = [
        _fake_search_result("knowledge/star-citizen.md", "Star Citizen is a space sim."),
        _fake_search_result("knowledge/ships.md", "The Carrack is an explorer ship."),
    ]

    state = _fake_app_state(vault_client=fake_client, config=_fake_config())

    with patch("shared.embeddings.embed_text", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
        result = await _fetch_vault_snippets(
            state, text="tell me about star citizen", device_audience=None,
        )

    assert len(result) == 2
    assert "knowledge/star-citizen.md" in result[0]
    assert "Star Citizen is a space sim" in result[0]
    assert "knowledge/ships.md" in result[1]


@pytest.mark.asyncio
async def test_fetch_vault_snippets_truncates_long_body():
    """Snippets are truncated to _VAULT_SNIPPET_MAX_CHARS."""
    long_body = "X" * (_VAULT_SNIPPET_MAX_CHARS + 100)
    fake_client = MagicMock()
    fake_client.search.return_value = [
        _fake_search_result("knowledge/long.md", long_body),
    ]
    state = _fake_app_state(vault_client=fake_client, config=_fake_config())

    with patch("shared.embeddings.embed_text", new=AsyncMock(return_value=[0.1])):
        result = await _fetch_vault_snippets(
            state, text="anything", device_audience=None,
        )

    assert len(result) == 1
    # The snippet body portion must not exceed max chars + "..."
    assert result[0].endswith("...")
    # The [path] prefix + body truncated to max
    body_part = result[0].split("] ", 1)[1]
    assert len(body_part) <= _VAULT_SNIPPET_MAX_CHARS + 3  # + "..."


@pytest.mark.asyncio
async def test_fetch_vault_snippets_swallows_embed_error():
    """If the embedder raises, return empty list without propagating."""
    fake_client = MagicMock()
    state = _fake_app_state(vault_client=fake_client, config=_fake_config())

    with patch(
        "shared.embeddings.embed_text",
        new=AsyncMock(side_effect=RuntimeError("ollama down")),
    ):
        result = await _fetch_vault_snippets(
            state, text="test", device_audience=None,
        )

    assert result == []


@pytest.mark.asyncio
async def test_fetch_vault_snippets_timeout_returns_empty():
    """If the search takes longer than the timeout, return empty."""
    import asyncio as _asyncio

    fake_client = MagicMock()
    state = _fake_app_state(vault_client=fake_client, config=_fake_config())

    async def _slow_embed(*args, **kwargs):
        await _asyncio.sleep(10)
        return [0.1]

    with patch("shared.embeddings.embed_text", new=_slow_embed), \
         patch(
             "gateway.hive_turn_helpers._VAULT_SEARCH_TIMEOUT_S",
             0.05,
         ):
        result = await _fetch_vault_snippets(
            state, text="test", device_audience=None,
        )

    assert result == []


# ---------------------------------------------------------------- build_turn_context_async


@pytest.mark.asyncio
async def test_build_turn_context_async_injects_snippets():
    """build_turn_context_async must populate vault_snippets on TurnContext."""
    fake_client = MagicMock()
    fake_client.search.return_value = [
        _fake_search_result(
            "knowledge/origin-300.md",
            "The 300i is an Origin Jumpworks touring ship prized for luxury.",
        ),
    ]

    state = _fake_app_state(vault_client=fake_client, config=_fake_config())
    state.image_build_store = None
    state.skill_registry = None
    state.memory_store_terry = None
    state.helpers = {}

    with patch("shared.embeddings.embed_text", new=AsyncMock(return_value=[0.5, 0.6])):
        ctx = await build_turn_context_async(
            state,
            user_id=42,
            text="tell me about the 300i ship",
            device_id="dev-test",
            device_audience=["terry"],
            thread_id="default",
        )

    assert len(ctx.vault_snippets) == 1
    assert "origin-300.md" in ctx.vault_snippets[0]
    assert "Origin Jumpworks" in ctx.vault_snippets[0]


@pytest.mark.asyncio
async def test_build_turn_context_async_still_works_without_vault():
    """When no vault_client is set, vault_snippets stays empty but context is built."""
    state = _fake_app_state(vault_client=None, config=_fake_config())
    state.image_build_store = None
    state.skill_registry = None
    state.memory_store_terry = None
    state.helpers = {}

    ctx = await build_turn_context_async(
        state,
        user_id=1,
        text="hello",
        device_id="dev-test",
        device_audience=None,
    )

    assert isinstance(ctx, TurnContext)
    assert ctx.vault_snippets == []
    assert ctx.user_msg == "hello"


# ---------------------------------------------------------------- planner input injection


@pytest.mark.asyncio
async def test_planner_receives_retrieved_knowledge_when_snippets_present():
    """HiveCoordinator._plan must pass retrieved_knowledge to the planner task."""
    from pathlib import Path

    from gateway.event_emitter import ListEmitter
    from gateway.helpers.base import HelperResult, HelperTask
    from gateway.hive_coordinator import HiveCoordinator, TurnContext
    from gateway.model_catalog import load_catalog

    catalog = load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )

    class _CapturingHelper:
        def __init__(self, role: str, output: dict) -> None:
            self.role = role
            self.model_id = "test-model"
            self.invoked_with: list[HelperTask] = []
            self._output = output

        async def invoke(self, task: HelperTask) -> HelperResult:
            self.invoked_with.append(task)
            return HelperResult(
                role=self.role, model_id=self.model_id,
                output=self._output, plan=[],
                confidence="high",
                tokens_in=5, tokens_out=10, latency_ms=1,
                parent_id=task.parent_id,
            )

    planner = _CapturingHelper(
        "planner",
        {
            "summary": "direct reply test",
            "delegations": [],
            "direct_reply": "Here is the answer.",
            "confidence": "high",
        },
    )
    synth = _CapturingHelper(
        "synthesizer",
        {"reply": "ok", "actions": []},
    )

    helpers = {"planner": planner, "synthesizer": synth}
    coord = HiveCoordinator(catalog, helpers)

    snippet_text = "The Carrack is a dedicated exploration ship by Anvil."
    ctx = TurnContext(
        user_msg="tell me about the Carrack",
        user_id=99,
        device_id="dev-x",
        vault_snippets=[f"[knowledge/carrack.md] {snippet_text}"],
    )

    emitter = ListEmitter()
    await coord.coordinate(ctx, emitter)

    assert planner.invoked_with, "planner must have been invoked"
    inputs = planner.invoked_with[0].inputs
    assert "retrieved_knowledge" in inputs, (
        "planner task inputs must contain 'retrieved_knowledge'"
    )
    assert snippet_text in inputs["retrieved_knowledge"], (
        "retrieved_knowledge must contain the vault snippet text"
    )


@pytest.mark.asyncio
async def test_planner_retrieved_knowledge_empty_when_no_snippets():
    """When vault_snippets is empty, retrieved_knowledge passed to planner is ''."""
    from pathlib import Path

    from gateway.event_emitter import ListEmitter
    from gateway.helpers.base import HelperResult, HelperTask
    from gateway.hive_coordinator import HiveCoordinator, TurnContext
    from gateway.model_catalog import load_catalog

    catalog = load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )

    class _CapturingHelper:
        def __init__(self, role: str, output: dict) -> None:
            self.role = role
            self.model_id = "test-model"
            self.invoked_with: list[HelperTask] = []
            self._output = output

        async def invoke(self, task: HelperTask) -> HelperResult:
            self.invoked_with.append(task)
            return HelperResult(
                role=self.role, model_id=self.model_id,
                output=self._output, plan=[],
                confidence="high",
                tokens_in=5, tokens_out=10, latency_ms=1,
                parent_id=task.parent_id,
            )

    planner = _CapturingHelper(
        "planner",
        {
            "summary": "no snippets test",
            "delegations": [],
            "direct_reply": "Fine.",
            "confidence": "high",
        },
    )
    helpers = {"planner": planner}
    coord = HiveCoordinator(catalog, helpers)

    ctx = TurnContext(
        user_msg="hello",
        user_id=1,
        device_id="dev-y",
        vault_snippets=[],  # explicitly empty
    )

    emitter = ListEmitter()
    await coord.coordinate(ctx, emitter)

    assert planner.invoked_with
    retrieved = planner.invoked_with[0].inputs.get("retrieved_knowledge", "MISSING")
    assert retrieved == "", (
        f"retrieved_knowledge must be '' when vault_snippets is empty; got {retrieved!r}"
    )
