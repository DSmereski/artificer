"""Tests for OllamaAdapter — probe + run via local Ollama HTTP."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from hive_node_agent.runtimes.ollama import OllamaAdapter


@pytest.mark.asyncio
async def test_probe_when_running_returns_installed_true() -> None:
    a = OllamaAdapter()

    async def fake_get(self, url, **kwargs):
        # Match `client.get(...)` signature.
        return httpx.Response(
            200,
            json={"models": [
                {"name": "qwen2.5:1.5b", "details": {}},
                {"name": "nomic-embed-text", "details": {}},
            ]},
            request=httpx.Request("GET", url),
        )

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        result = await a.probe()
    assert result["installed"] is True
    assert "qwen2.5:1.5b" in result["models"]


@pytest.mark.asyncio
async def test_probe_when_unreachable_returns_installed_false() -> None:
    a = OllamaAdapter()

    async def fake_get(self, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    with patch.object(httpx.AsyncClient, "get", new=fake_get):
        result = await a.probe()
    assert result["installed"] is False


@pytest.mark.asyncio
async def test_run_generate_returns_done_with_text() -> None:
    a = OllamaAdapter()

    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:1.5b",
                "response": "hello world",
                "done": True,
            },
            request=httpx.Request("POST", url),
        )

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        result = await a.run({
            "model": "qwen2.5:1.5b",
            "prompt": "say hi",
        })
    assert result.status == "done"
    assert result.output["response"] == "hello world"
    assert result.output["model"] == "qwen2.5:1.5b"
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_run_with_http_error_returns_error() -> None:
    a = OllamaAdapter()

    async def fake_post(self, url, **kwargs):
        return httpx.Response(
            404,
            json={"error": "model 'no-such' not found"},
            request=httpx.Request("POST", url),
        )

    with patch.object(httpx.AsyncClient, "post", new=fake_post):
        result = await a.run({"model": "no-such", "prompt": "hi"})
    assert result.status == "error"
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_run_validates_required_fields() -> None:
    a = OllamaAdapter()
    # No model.
    result = await a.run({"prompt": "hi"})
    assert result.status == "error"
    assert "model" in result.error.lower()
