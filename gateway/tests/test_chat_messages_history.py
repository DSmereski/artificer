"""Tests for GET /v1/chat/{bot}/messages history endpoint.

Specifically verifies that text turns survive relaunch even when they have
aged out of the LLMClient in-memory rolling buffer — the root cause of the
"text messages missing after relaunch" bug discovered on 2026-04-29.

Root cause: chat_messages() read exclusively from LLMClient._history (a
rolling buffer capped at _MAX_HISTORY=200 entries) and ignored the
persistent chat_log SQLite table that index_hive_turn_to_chat_log() writes
every turn.  On relaunch, after the gateway process restarted and _history
was re-loaded from the JSON file (itself bounded to the last 200 entries),
any history older than 200 messages was lost.

Fix: chat_messages() now consults VaultClient.recent_chat() first (reads
from chat_log) and only falls back to the in-memory buffer when the
persistent table is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import (
    Config,
    NtfyConfig,
    PairingConfig,
    RateLimits,
    VaultWriterConfig,
)
from gateway.deps import AppState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client(
    tmp_path: Path,
    *,
    vault_client: Any = None,
    llm_recent_messages: list[dict] | None = None,
) -> tuple[TestClient, dict]:
    """Build a TestClient with a minimal AppState wired for the messages
    endpoint.  Returns (client, paired_token_dict)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()

    cfg = Config(
        bind_host="127.0.0.1",
        bind_port=0,
        tailscale_bind=None,
        state_dir=state_dir,
        vault_path=vault,
        vault_writer=VaultWriterConfig(
            host="127.0.0.1", port=8765,
            token_path=tmp_path / "does-not-exist",
        ),
        history_roots={},
        models={},
        pairing=PairingConfig(code_ttl_seconds=60, code_length=8, token_bytes=16),
        ntfy=NtfyConfig(base_url="http://127.0.0.1:8080", enabled=False),
        rate_limits=RateLimits(writes_per_minute=60, images_per_hour=30),
    )

    app = create_app(cfg)
    prev = app.state.ai_team

    # Fake LLMClient with a configurable recent_messages return value.
    fake_llm = MagicMock()
    fake_llm.recent_messages.return_value = llm_recent_messages or []

    # Fake adapter that exposes the fake LLM.
    fake_adapter = MagicMock()
    fake_adapter.name = "hive"
    fake_adapter._llm = fake_llm

    app.state.ai_team = AppState(
        config=cfg,
        devices=prev.devices,
        pairing=prev.pairing,
        adapters={"hive": fake_adapter},
        vault_client=vault_client,
    )

    client = TestClient(app)

    # Pair a device so we have a real bearer token.
    r = client.get("/v1/pair/new")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post("/v1/pair", json={
        "code": code, "name": "test-device", "platform": "test",
    })
    assert r.status_code == 200, r.text
    return client, r.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_messages_returns_from_chat_log_when_available(tmp_path: Path) -> None:
    """Primary path: when VaultClient.recent_chat() returns rows, those rows
    are what the endpoint returns — regardless of the in-memory buffer."""
    persisted_rows = [
        {"role": "user", "content": "hello from persistent store"},
        {"role": "assistant", "content": "hi back from persistent store"},
    ]

    fake_vc = MagicMock()
    fake_vc.recent_chat.return_value = persisted_rows

    client, token_data = _build_client(
        tmp_path,
        vault_client=fake_vc,
        # Rolling buffer is deliberately empty — simulating a gateway
        # restart where _MAX_HISTORY entries fell off.
        llm_recent_messages=[],
    )
    token = token_data["token"]

    r = client.get(
        "/v1/chat/hive/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": "50"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "messages" in body
    assert body["messages"] == persisted_rows


def test_messages_falls_back_to_rolling_buffer_when_chat_log_empty(
    tmp_path: Path,
) -> None:
    """When chat_log is empty (no VaultClient or recent_chat returns []),
    the endpoint falls back to the in-memory rolling buffer so fresh
    sessions still work."""
    buffer_rows = [
        {"role": "user", "content": "hello from buffer"},
        {"role": "assistant", "content": "hi from buffer"},
    ]

    # VaultClient present but recent_chat returns nothing (brand-new session
    # before any turns have been indexed).
    fake_vc = MagicMock()
    fake_vc.recent_chat.return_value = []

    client, token_data = _build_client(
        tmp_path,
        vault_client=fake_vc,
        llm_recent_messages=buffer_rows,
    )
    token = token_data["token"]

    r = client.get(
        "/v1/chat/hive/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["messages"] == buffer_rows


def test_messages_falls_back_when_no_vault_client(tmp_path: Path) -> None:
    """When vault_client is None (test environments, bare gateway configs),
    the rolling buffer is still returned — endpoint never errors out."""
    buffer_rows = [
        {"role": "user", "content": "only in buffer"},
        {"role": "assistant", "content": "reply only in buffer"},
    ]

    client, token_data = _build_client(
        tmp_path,
        vault_client=None,
        llm_recent_messages=buffer_rows,
    )
    token = token_data["token"]

    r = client.get(
        "/v1/chat/hive/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["messages"] == buffer_rows


def test_messages_chat_log_preferred_over_shorter_buffer(tmp_path: Path) -> None:
    """Regression: the persistent chat_log holds 60 turns; the in-memory
    buffer only has 10 (simulating a gateway restart that loaded just the
    tail of the JSON file after aggressive trimming).  The endpoint must
    return the full 60 turns from chat_log, not the truncated 10."""
    # Build 60 turns in persistent store, only 10 in rolling buffer.
    persistent = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
        for i in range(60)
    ]
    buffer_only = persistent[-10:]  # what survived in the rolling buffer

    fake_vc = MagicMock()
    fake_vc.recent_chat.return_value = persistent

    client, token_data = _build_client(
        tmp_path,
        vault_client=fake_vc,
        llm_recent_messages=buffer_only,
    )
    token = token_data["token"]

    r = client.get(
        "/v1/chat/hive/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": "60"},
    )
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) == 60
    assert msgs[0]["content"] == "msg 0"   # oldest turn survived
    assert msgs[-1]["content"] == "msg 59"


def test_messages_vault_client_exception_falls_back_gracefully(
    tmp_path: Path,
) -> None:
    """If recent_chat raises (e.g. DB locked under high load), the endpoint
    does not 500 — it falls back to the rolling buffer and logs a warning."""
    buffer_rows = [{"role": "user", "content": "fallback msg"}]

    fake_vc = MagicMock()
    fake_vc.recent_chat.side_effect = RuntimeError("simulated DB error")

    client, token_data = _build_client(
        tmp_path,
        vault_client=fake_vc,
        llm_recent_messages=buffer_rows,
    )
    token = token_data["token"]

    r = client.get(
        "/v1/chat/hive/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["messages"] == buffer_rows
