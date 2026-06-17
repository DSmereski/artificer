"""Anthropic Claude API benchmark adapter.

Mirrors the ollama_runtime contract: returns BenchInvocation with
output text, token count, latency. Token count is *output* tokens
(matches Ollama's eval_count semantics so downstream tokens-per-s
math stays consistent across runtimes).

The ``anthropic`` SDK is an optional dependency. If it isn't installed,
imports stay safe and ``invoke_claude`` raises a clear RuntimeError when
called — so users who never benchmark Claude are unaffected.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

try:
    from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
    _ANTHROPIC_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover — exercised only without SDK
    AsyncAnthropic = None  # type: ignore[assignment,misc]
    _ANTHROPIC_AVAILABLE = False


@dataclass(frozen=True)
class BenchInvocation:
    output: str
    token_count: int
    latency_ms: float


def _build_async_client(api_key: str):
    """Construct an AsyncAnthropic client. Patched out in tests so we
    never hit the real SDK during unit tests."""
    if not _ANTHROPIC_AVAILABLE:
        raise RuntimeError(
            "anthropic SDK not installed; "
            "run `pip install anthropic` to benchmark Claude models",
        )
    return AsyncAnthropic(api_key=api_key)


async def invoke_claude(
    *,
    api_key: str,
    model: str,
    prompt: str,
    max_tokens: int,
) -> BenchInvocation:
    client = _build_async_client(api_key)
    start = time.perf_counter()
    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    latency_ms = (time.perf_counter() - start) * 1000
    output_text = "".join(
        getattr(block, "text", "") for block in msg.content
    )
    return BenchInvocation(
        output=output_text,
        token_count=int(msg.usage.output_tokens),
        latency_ms=latency_ms,
    )
