"""Tests for the Anthropic Claude benchmark runtime."""
from __future__ import annotations
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.orchestrator.runtimes.claude_runtime import (
    BenchInvocation,
    invoke_claude,
)


@pytest.mark.asyncio
async def test_invoke_claude_returns_invocation():
    fake_message = MagicMock()
    fake_message.content = [MagicMock(text="the kraken sleeps in the deep")]
    fake_message.usage.input_tokens = 12
    fake_message.usage.output_tokens = 8

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_message)

    with patch(
        "gateway.orchestrator.runtimes.claude_runtime._build_async_client",
        return_value=fake_client,
    ):
        inv = await invoke_claude(
            api_key="sk-fake",
            model="claude-haiku-4-5-20251001",
            prompt="where does the kraken sleep?",
            max_tokens=200,
        )

    assert isinstance(inv, BenchInvocation)
    assert inv.output == "the kraken sleeps in the deep"
    assert inv.token_count == 8
    assert inv.latency_ms > 0


@pytest.mark.asyncio
async def test_invoke_claude_raises_when_sdk_missing():
    """If the anthropic SDK is not installed AND _build_async_client
    isn't patched, invoke_claude should raise a clear error pointing the
    user to `pip install anthropic`."""
    with patch(
        "gateway.orchestrator.runtimes.claude_runtime._ANTHROPIC_AVAILABLE",
        False,
    ):
        with pytest.raises(RuntimeError, match="anthropic"):
            await invoke_claude(
                api_key="sk-fake",
                model="claude-haiku-4-5-20251001",
                prompt="x",
                max_tokens=10,
            )
