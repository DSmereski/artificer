"""Tests for the ollama benchmark runtime — invoke a model with a prompt
and return latency + tokens + output. httpx is mocked so tests don't
need a live Ollama."""
from __future__ import annotations
from unittest.mock import AsyncMock, patch

import pytest

from gateway.orchestrator.runtimes.ollama_runtime import (
    BenchInvocation,
    invoke_ollama,
)


@pytest.mark.asyncio
async def test_invoke_ollama_returns_invocation():
    fake_response = {
        "response": "the kraken sleeps in the deep",
        "done": True,
        "eval_count": 8,
        "eval_duration": 100_000_000,
    }

    async def fake_post(url, json=None, timeout=None):
        class _Resp:
            status_code = 200
            text = ""
            def json(self): return fake_response
        return _Resp()

    with patch(
        "gateway.orchestrator.runtimes.ollama_runtime.httpx.AsyncClient"
    ) as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_client.return_value = mock_instance

        inv = await invoke_ollama(
            host_url="http://localhost:11434",
            model="planner-qwen",
            prompt="where does the kraken sleep?",
            max_tokens=200,
        )

    assert isinstance(inv, BenchInvocation)
    assert inv.output == "the kraken sleeps in the deep"
    assert inv.token_count == 8
    assert inv.latency_ms > 0


@pytest.mark.asyncio
async def test_invoke_ollama_passes_num_gpu_when_provided():
    """When num_gpu=0 is supplied (CPU-only model bench), the request body
    must include options.num_gpu=0 so Ollama runs on CPU not GPU."""
    captured: dict = {}

    async def fake_post(url, json=None, timeout=None):
        captured["json"] = json

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"response": "ok", "eval_count": 2}

        return _Resp()

    with patch(
        "gateway.orchestrator.runtimes.ollama_runtime.httpx.AsyncClient"
    ) as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_client.return_value = mock_instance

        await invoke_ollama(
            host_url="http://localhost:11434",
            model="gemma3-ablit-4b",
            prompt="x",
            max_tokens=10,
            num_gpu=0,
        )

    assert captured["json"]["options"]["num_gpu"] == 0


@pytest.mark.asyncio
async def test_invoke_ollama_omits_num_gpu_when_none():
    """Default behaviour (no num_gpu) leaves Ollama free to use any GPU."""
    captured: dict = {}

    async def fake_post(url, json=None, timeout=None):
        captured["json"] = json

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"response": "ok", "eval_count": 2}

        return _Resp()

    with patch(
        "gateway.orchestrator.runtimes.ollama_runtime.httpx.AsyncClient"
    ) as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_client.return_value = mock_instance

        await invoke_ollama(
            host_url="http://localhost:11434",
            model="planner-qwen",
            prompt="x",
            max_tokens=10,
        )

    assert "num_gpu" not in captured["json"]["options"]


@pytest.mark.asyncio
async def test_invoke_ollama_raises_on_http_error():
    async def fake_post(url, json=None, timeout=None):
        class _Resp:
            status_code = 500
            text = "internal error"
            def json(self): return {}
        return _Resp()

    with patch(
        "gateway.orchestrator.runtimes.ollama_runtime.httpx.AsyncClient"
    ) as mock_client:
        mock_instance = AsyncMock()
        mock_instance.post = AsyncMock(side_effect=fake_post)
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_client.return_value = mock_instance

        with pytest.raises(RuntimeError, match="500"):
            await invoke_ollama(
                host_url="http://localhost:11434",
                model="planner-qwen",
                prompt="x",
                max_tokens=10,
            )
