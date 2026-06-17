"""Tests for gateway.composio.client.

These run without the real Composio SDK installed — the module's
contract is "graceful no-op when SDK missing or key unset." The one
test that *does* hit the live path monkeypatches `_invoke_sdk` so no
network call ever leaves the box.
"""

from __future__ import annotations

import os

import pytest

from gateway.composio.client import (
    ComposioClient,
    ComposioResult,
    is_available,
)


def test_unavailable_when_no_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    assert is_available() is False


def test_client_unavailable_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    c = ComposioClient()
    r = c.execute(app="slack", action="postMessage", args={"text": "hi"})
    assert r.ok is False
    assert r.error == "composio_unavailable"
    d = r.to_dict()
    assert d == {
        "ok": False,
        "error": "composio_unavailable",
        "result": d["result"],
    }
    assert d["result"]["missing_key"] is True


def test_client_does_not_raise_without_key():
    # Construction must be total; never throws.
    c = ComposioClient(api_key=None)
    assert c.available is False


def test_missing_app_returns_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "fake-key")
    c = ComposioClient()
    # Force "available" so we exercise the validation branch.
    object.__setattr__(c, "_sdk_present", True)
    r = c.execute(app="", action="x")
    assert r.ok is False
    assert r.error == "missing_app"


def test_missing_action_returns_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "fake-key")
    c = ComposioClient()
    object.__setattr__(c, "_sdk_present", True)
    r = c.execute(app="slack", action="")
    assert r.ok is False
    assert r.error == "missing_action"


def test_execute_dispatches_to_sdk_seam(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "fake-key")
    c = ComposioClient()
    # Pretend SDK is present and replace the seam.
    object.__setattr__(c, "_sdk_present", True)
    captured: dict = {}

    def fake_invoke(app, action, args):
        captured.update(app=app, action=action, args=args)
        return {"ts": "1234.5678", "channel": "ai-chat"}

    object.__setattr__(c, "_invoke_sdk", fake_invoke)
    r = c.execute(app="slack", action="postMessage", args={"text": "hi"})
    assert r.ok is True
    assert r.error is None
    assert r.result == {"ts": "1234.5678", "channel": "ai-chat"}
    assert captured == {
        "app": "slack", "action": "postMessage", "args": {"text": "hi"},
    }


def test_sdk_exception_caught_as_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMPOSIO_API_KEY", "fake-key")
    c = ComposioClient()
    object.__setattr__(c, "_sdk_present", True)

    def boom(app, action, args):
        raise RuntimeError("upstream timeout")

    object.__setattr__(c, "_invoke_sdk", boom)
    r = c.execute(app="slack", action="postMessage")
    assert r.ok is False
    assert r.error is not None and r.error.startswith("sdk_error")


def test_result_dataclass_is_serialisable():
    r = ComposioResult(ok=True, error=None, result={"a": 1})
    d = r.to_dict()
    assert d["ok"] is True
    assert d["result"] == {"a": 1}
