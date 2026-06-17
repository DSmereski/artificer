"""Shared pytest fixtures for gateway tests."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

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


class _FakeAdapter:
    def __init__(self, name: str, reply: str = "hi from fake") -> None:
        self.name = name
        self.display_name = name.capitalize()
        self._reply = reply

    def status(self) -> str:
        return "online"

    async def reply_stream(
        self, user_id: int, text: str, *, extra_system: str = "",
    ) -> AsyncIterator[str]:
        del extra_system  # ignored in fake
        yield self._reply

    async def reply(
        self, user_id: int, text: str, *, extra_system: str = "",
    ) -> str:
        # The Terry special-path in routes/chat.py calls reply() for
        # marker scanning. The fake just returns its canned string.
        del user_id, text, extra_system
        return self._reply

    def reset_history(self, user_id: int) -> None:  # noqa: D401
        del user_id


class _FakeHiveCoordinator:
    """Stand-in for HiveCoordinator that just emits the fake adapter's
    reply via the chat WS emitter. Used by tests that assert
    streaming-shape responses without needing the real planner/synth
    helper graph (which itself needs Ollama)."""

    def __init__(self, adapter: _FakeAdapter) -> None:
        self._adapter = adapter

    async def coordinate(self, ctx, emitter):
        import uuid
        from gateway.hive_coordinator import AssistantTurn
        reply = await self._adapter.reply(ctx.user_id, ctx.user_msg)
        # Production coordinator stamps a turn_id; mirror it so the
        # dispatcher's trailing `done` frame carries it the same way
        # in tests as it does in production.
        turn_id = f"tk-{uuid.uuid4().hex[:8]}"
        emitter.assistant(reply, parent_id=turn_id)
        return AssistantTurn(
            reply=reply, helpers_used=[], total_tokens=0,
            turn_id=turn_id,
        )


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def tmp_config(tmp_state_dir: Path, tmp_path: Path) -> Config:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Config(
        bind_host="127.0.0.1",
        bind_port=0,
        tailscale_bind=None,
        state_dir=tmp_state_dir,
        vault_writer=VaultWriterConfig(
            host="127.0.0.1", port=8765,
            token_path=tmp_path / "does-not-exist",
        ),
        vault_path=vault,
        history_roots={},
        models={},
        pairing=PairingConfig(code_ttl_seconds=60, code_length=8, token_bytes=16),
        ntfy=NtfyConfig(base_url="http://127.0.0.1:8080", enabled=False),
        rate_limits=RateLimits(writes_per_minute=60, images_per_hour=30),
    )


@pytest.fixture
def client(tmp_config: Config) -> TestClient:
    """FastAPI TestClient. Injects a fake adapter set so no Ollama required."""
    app = create_app(tmp_config)
    prev = app.state.ai_team
    # Tests share the `testclient` IP across many calls in one suite. The
    # production pair_attempts bucket (10/min, burst 5) trips after a few
    # `/v1/pair/new` calls. Re-register with a much higher ceiling so
    # tests exercising the pair flow aren't artificially throttled. The
    # security test for rate-limiting still works — it tightens the
    # bucket explicitly.
    prev.rate_limiter.register("pair_attempts", per_minute=200, burst=30)
    fake_terry = _FakeAdapter("terry", reply="Terry says hello")
    app.state.ai_team = AppState(
        config=tmp_config,
        devices=prev.devices,
        pairing=prev.pairing,
        adapters={
            # M1: Maggy and Scout are decommissioned. Their /v1/chat/<bot>
            # URLs soft-redirect to Terry; tests for that path live in
            # test_legacy_redirect.py.
            "terry": fake_terry,
        },
        scout_history=prev.scout_history,
        image_shim=prev.image_shim,
        event_bus=prev.event_bus,
        ntfy=prev.ntfy,
        # Fake coordinator so the chat WS happy-path tests don't need
        # the full planner/synth helper graph (or Ollama).
        hive_coordinator=_FakeHiveCoordinator(fake_terry),
        node_registry=prev.node_registry,
        node_invites=prev.node_invites,
        dispatcher=prev.dispatcher,
        scheduler=prev.scheduler,
        rate_limiter=prev.rate_limiter,
    )
    return TestClient(app)


@pytest.fixture
def paired_token(client: TestClient) -> tuple[str, str]:
    """Return (device_id, token) for a freshly paired device."""
    r = client.get("/v1/pair/new")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = client.post("/v1/pair", json={"code": code, "name": "pytest-device", "platform": "test"})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["device_id"], data["token"]
