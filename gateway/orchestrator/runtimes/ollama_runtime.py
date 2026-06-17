"""Synchronous Ollama HTTP invocation, instrumented for benchmarking.

Returns ``BenchInvocation`` (latency + token count + output text). NOT
the path used by hive node agents at runtime — that path is in the node
agent. This adapter is host-side, used only by the bench harness to time
models against canonical prompts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class BenchInvocation:
    output: str
    token_count: int
    latency_ms: float


async def invoke_ollama(
    *,
    host_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout_s: float = 60.0,
    num_gpu: int | None = None,
) -> BenchInvocation:
    options: dict = {"num_predict": max_tokens}
    # When the caller pins num_gpu (e.g., 0 for CPU-only models such as
    # gemma3-4b registered with gpu_vram_mb=0), forward it so the
    # bench measures the same path the production helper will take.
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": options,
    }
    start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{host_url}/api/generate",
            json=payload,
            timeout=timeout_s,
        )
    latency_ms = (time.perf_counter() - start) * 1000
    if resp.status_code >= 400:
        raise RuntimeError(
            f"ollama {model} returned {resp.status_code}: "
            f"{resp.text[:200]}",
        )
    body = resp.json()
    return BenchInvocation(
        output=str(body.get("response", "")),
        token_count=int(body.get("eval_count", 0)),
        latency_ms=latency_ms,
    )
