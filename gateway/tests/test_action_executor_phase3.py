"""Phase 3 (#456): action executor coverage for the new memory + entity verbs.

The shipped wiring covers `core_memory_replace`, `core_memory_append`,
and `entity_page_update` but had no per-verb unit tests. These lock
behaviour for: (a) the slot-write happy path, (b) the missing-store
sad path, (c) relationships pass-through with confidence validation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.action_executor import ActionExecutor


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- core_memory_replace


def test_core_memory_replace_writes_slot(tmp_path: Path) -> None:
    from gateway.conversation_memory import MemoryStore
    store = MemoryStore(tmp_path, bot="hive")
    ex = ActionExecutor(memory_store=store)
    receipts = _run(ex.execute_all(
        [{"verb": "core_memory_replace",
          "payload": {"slot": "preferences",
                      "content": "user prefers terse replies"}}],
        user_id=42, thread_id="default", bot="hive",
    ))
    assert len(receipts) == 1
    assert receipts[0].ok is True
    assert receipts[0].verb == "core_memory_replace"
    mem = store.get(42, "default")
    assert mem.core_slots["preferences"].content == (
        "user prefers terse replies"
    )


def test_core_memory_replace_rejects_missing_slot() -> None:
    ex = ActionExecutor(memory_store=MagicMock())
    receipts = _run(ex.execute_all(
        [{"verb": "core_memory_replace",
          "payload": {"content": "x"}}],
        user_id=42,
    ))
    assert receipts[0].ok is False
    assert "slot" in receipts[0].detail


def test_core_memory_replace_no_store_configured() -> None:
    """Without a memory_store the receipt must fail loudly (not crash)."""
    ex = ActionExecutor(memory_store=None)
    receipts = _run(ex.execute_all(
        [{"verb": "core_memory_replace",
          "payload": {"slot": "preferences", "content": "x"}}],
        user_id=42,
    ))
    assert receipts[0].ok is False
    assert "memory store" in receipts[0].detail


# ---------------------------------------------------------------- core_memory_append


def test_core_memory_append_extends_slot(tmp_path: Path) -> None:
    from gateway.conversation_memory import MemoryStore
    store = MemoryStore(tmp_path, bot="hive")
    store.set_core_slot(7, name="active_projects",
                        content="phase-1-rollout")
    ex = ActionExecutor(memory_store=store)
    _run(ex.execute_all(
        [{"verb": "core_memory_append",
          "payload": {"slot": "active_projects",
                      "content": "phase-3-rollout"}}],
        user_id=7, thread_id="default", bot="hive",
    ))
    mem = store.get(7, "default")
    txt = mem.core_slots["active_projects"].content
    assert "phase-1-rollout" in txt
    assert "phase-3-rollout" in txt


def test_core_memory_append_requires_content() -> None:
    ex = ActionExecutor(memory_store=MagicMock())
    receipts = _run(ex.execute_all(
        [{"verb": "core_memory_append",
          "payload": {"slot": "preferences", "content": ""}}],
        user_id=42,
    ))
    assert receipts[0].ok is False


# ---------------------------------------------------------------- entity_page_update relationships


def test_entity_page_update_passes_relationships_to_client() -> None:
    """Phase 3 (#456): the executor must forward a relationships list
    verbatim to the VaultClient RPC."""
    fake_client = MagicMock()
    fake_client.entity_page_update = AsyncMock(
        return_value={"ok": True, "prior_compiled_truth": "",
                      "prior_existed": False},
    )
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    edges = [
        {"target_slug": "drake-interplanetary",
         "label": "manufactured_by", "confidence": "EXTRACTED"},
        {"target_slug": "user-org",
         "label": "is_flagship_of", "confidence": "INFERRED"},
    ]
    receipts = _run(ex.execute_all(
        [{"verb": "entity_page_update",
          "payload": {
              "id": "kraken",
              "kind": "thing",
              "title": "Kraken",
              "compiled_truth": "Drake heavy carrier.",
              "relationships": edges,
          }}],
        user_id=42, bot="hive",
    ))
    assert receipts[0].ok is True
    fake_client.entity_page_update.assert_awaited_once()
    kwargs = fake_client.entity_page_update.await_args.kwargs
    assert kwargs["relationships"] == edges


def test_entity_page_update_rejects_bad_confidence_label() -> None:
    fake_client = MagicMock()
    fake_client.entity_page_update = AsyncMock(return_value={"ok": True})
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    receipts = _run(ex.execute_all(
        [{"verb": "entity_page_update",
          "payload": {
              "id": "kraken", "kind": "thing", "title": "Kraken",
              "relationships": [
                  {"target_slug": "drake", "label": "made_by",
                   "confidence": "MAYBE"}
              ],
          }}],
        user_id=42, bot="hive",
    ))
    assert receipts[0].ok is False
    assert "confidence" in receipts[0].detail.lower()
    fake_client.entity_page_update.assert_not_called()


def test_entity_page_update_omits_relationships_when_absent() -> None:
    """No `relationships` key in payload -> the kwarg is None and the
    RPC client preserves any existing edges on the row."""
    fake_client = MagicMock()
    fake_client.entity_page_update = AsyncMock(
        return_value={"ok": True, "prior_compiled_truth": "",
                      "prior_existed": False},
    )
    ex = ActionExecutor(vault_client_factory=lambda: fake_client)
    _run(ex.execute_all(
        [{"verb": "entity_page_update",
          "payload": {"id": "kraken", "kind": "thing",
                      "title": "Kraken",
                      "compiled_truth": "x"}}],
        user_id=42, bot="hive",
    ))
    kwargs = fake_client.entity_page_update.await_args.kwargs
    assert kwargs.get("relationships") is None
