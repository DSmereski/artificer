"""Tests for the saas_call verb on ActionExecutor (Phase B).

The verb wraps a ComposioClient.execute call. We don't talk to the live
SDK in tests — we hand the executor a fake client that mirrors the
ComposioResult contract (`ok`, `error`, `result`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from gateway.action_executor import ActionExecutor


@dataclass
class _FakeResult:
    ok: bool
    error: str | None = None
    result: Any = None


class _FakeComposio:
    """Mimics gateway.composio.client.ComposioClient."""

    def __init__(self, result: _FakeResult, *, raise_exc: Exception | None = None):
        self._result = result
        self._raise = raise_exc
        self.calls: list[tuple[str, str, dict]] = []

    def execute(self, *, app: str, action: str, args: dict | None = None):
        self.calls.append((app, action, dict(args or {})))
        if self._raise is not None:
            raise self._raise
        return self._result


@pytest.mark.asyncio
async def test_saas_call_no_client_configured():
    ex = ActionExecutor()
    [r] = await ex.execute_all([
        {"verb": "saas_call", "payload": {"app": "slack", "action": "post"}},
    ])
    assert r.ok is False
    assert "not configured" in r.detail


@pytest.mark.asyncio
async def test_saas_call_missing_app():
    ex = ActionExecutor(composio_client=_FakeComposio(_FakeResult(ok=True)))
    [r] = await ex.execute_all([
        {"verb": "saas_call", "payload": {"action": "post"}},
    ])
    assert r.ok is False
    assert "missing app" in r.detail


@pytest.mark.asyncio
async def test_saas_call_missing_action():
    ex = ActionExecutor(composio_client=_FakeComposio(_FakeResult(ok=True)))
    [r] = await ex.execute_all([
        {"verb": "saas_call", "payload": {"app": "slack"}},
    ])
    assert r.ok is False
    assert "missing action" in r.detail


@pytest.mark.asyncio
async def test_saas_call_args_must_be_dict():
    ex = ActionExecutor(composio_client=_FakeComposio(_FakeResult(ok=True)))
    [r] = await ex.execute_all([
        {"verb": "saas_call", "payload": {"app": "slack", "action": "post", "args": ["x"]}},
    ])
    assert r.ok is False
    assert "args" in r.detail


@pytest.mark.asyncio
async def test_saas_call_unavailable_passes_through():
    fake = _FakeComposio(
        _FakeResult(ok=False, error="composio_unavailable", result={"missing_key": True}),
    )
    ex = ActionExecutor(composio_client=fake)
    [r] = await ex.execute_all([
        {"verb": "saas_call", "payload": {"app": "slack", "action": "post"}},
    ])
    assert r.ok is False
    assert r.detail == "composio_unavailable"
    assert r.payload["app"] == "slack"
    assert r.payload["result"] == {"missing_key": True}


@pytest.mark.asyncio
async def test_saas_call_happy_path():
    fake = _FakeComposio(_FakeResult(ok=True, result={"ts": "1234.5", "channel": "ai-chat"}))
    ex = ActionExecutor(composio_client=fake)
    [r] = await ex.execute_all([
        {
            "verb": "saas_call",
            "payload": {
                "app": "slack", "action": "postMessage",
                "args": {"text": "hi", "channel": "#ai-chat"},
            },
        },
    ])
    assert r.ok is True
    assert "slack.postMessage" in r.detail
    assert r.payload["result"] == {"ts": "1234.5", "channel": "ai-chat"}
    assert fake.calls == [("slack", "postMessage", {"text": "hi", "channel": "#ai-chat"})]


@pytest.mark.asyncio
async def test_saas_call_client_exception_caught():
    fake = _FakeComposio(_FakeResult(ok=True), raise_exc=RuntimeError("boom"))
    ex = ActionExecutor(composio_client=fake)
    [r] = await ex.execute_all([
        {"verb": "saas_call", "payload": {"app": "slack", "action": "post"}},
    ])
    assert r.ok is False
    assert r.detail.startswith("RuntimeError")
