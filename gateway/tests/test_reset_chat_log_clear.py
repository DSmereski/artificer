"""Integration test for POST /v1/chat/{bot}/reset wiring.

Verifies that the reset route handler passes on_chat_log_clear to
MemoryStore.reset so that vault chat_log rows are wiped alongside the
in-process sidecar on a live reset call.

Security finding #444 / commit 71da65e: without this wiring, the vault
daemon's chat_log table is never cleared and prior-conversation context
leaks back into new sessions via VaultClient.recent_chat().
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
    vault_client=None,
    memory_store=None,
) -> tuple[TestClient, dict]:
    """Build a TestClient wired for the reset route.

    Returns (client, paired_token_data).
    """
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

    # Fake LLM with a reset_history method so the handler's guard passes.
    fake_llm = MagicMock()
    fake_llm.reset_history = MagicMock()

    # Fake adapter that exposes the fake LLM.
    fake_adapter = MagicMock()
    fake_adapter.name = "terry"
    fake_adapter._llm = fake_llm

    ai_team = AppState(
        config=cfg,
        devices=prev.devices,
        pairing=prev.pairing,
        adapters={"terry": fake_adapter},
        vault_client=vault_client,
    )
    # Attach the optional memory_store_terry so the reset block can reach it.
    if memory_store is not None:
        ai_team.memory_store_terry = memory_store

    app.state.ai_team = ai_team

    client = TestClient(app)

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


def test_reset_calls_vault_client_chat_log_clear(tmp_path: Path) -> None:
    """#444: POST /v1/chat/terry/reset must call vault_client.chat_log_clear
    with the correct bot and user_id so that the daemon's chat_log rows are
    wiped alongside the in-process MemoryStore sidecar.

    Without this wiring the LOW security finding from commit 71da65e is only
    half-mitigated: the sidecar is gone but SQLite rows survive, leaking
    prior-conversation context into the next session via recent_chat().
    """
    # Track async calls to chat_log_clear.
    clear_calls: list[dict] = []

    async def _fake_chat_log_clear(*, bot: str, user_id: int) -> dict:
        clear_calls.append({"bot": bot, "user_id": user_id})
        return {"ok": True, "deleted": 3}

    fake_vc = MagicMock()
    fake_vc.chat_log_clear = _fake_chat_log_clear

    # Minimal MemoryStore stand-in that records reset() calls.
    reset_calls: list[dict] = []

    class _FakeMemoryStore:
        def reset(
            self,
            user_id: int,
            thread_id=None,
            *,
            on_chat_log_clear=None,
        ) -> None:
            reset_calls.append(
                {"user_id": user_id, "thread_id": thread_id,
                 "has_callback": on_chat_log_clear is not None}
            )
            # Actually invoke the callback so we can verify the vault call.
            if on_chat_log_clear is not None:
                on_chat_log_clear(user_id, "terry")

    client, token_data = _build_client(
        tmp_path,
        vault_client=fake_vc,
        memory_store=_FakeMemoryStore(),
    )
    token = token_data["token"]

    r = client.post(
        "/v1/chat/terry/reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # The reset must have been called with a callback.
    assert reset_calls, "memory_store.reset was not called"
    assert reset_calls[0]["has_callback"] is True, (
        "reset_chat must pass on_chat_log_clear to memory_store.reset"
    )

    # The callback must have scheduled a vault_client.chat_log_clear call.
    # TestClient runs in the same event loop context, so the task fires
    # synchronously before the response is returned.
    assert clear_calls, (
        "vault_client.chat_log_clear was not called — #444 still open"
    )
    assert clear_calls[0]["bot"] == "terry"
    # user_id is derived deterministically from device.user via _stable_user_id;
    # we only assert it's an integer, not the exact value, because the device
    # username is assigned by the fake pairing fixture.
    assert isinstance(clear_calls[0]["user_id"], int)


def test_reset_without_vault_client_does_not_raise(tmp_path: Path) -> None:
    """When vault_client is None (e.g., daemon offline), the reset route
    must still succeed — vault clearing is best-effort."""
    client, token_data = _build_client(tmp_path, vault_client=None)
    token = token_data["token"]

    r = client.post(
        "/v1/chat/terry/reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_reset_unknown_bot_returns_404(tmp_path: Path) -> None:
    """Calling reset for a bot that doesn't exist must return 404."""
    client, token_data = _build_client(tmp_path, vault_client=None)
    token = token_data["token"]

    r = client.post(
        "/v1/chat/unknown-bot/reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404, r.text


def test_reset_requires_auth(tmp_path: Path) -> None:
    """POST /v1/chat/{bot}/reset without a bearer token must be rejected."""
    client, _ = _build_client(tmp_path, vault_client=None)

    r = client.post("/v1/chat/terry/reset")
    assert r.status_code == 401, r.text
