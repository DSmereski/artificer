"""Pin the Ollama GPU-residency probe (#438).

The Ollama tray autostart drops `CUDA_VISIBLE_DEVICES=1,2`, targets the
gaming GPU0 (4080), and silently falls back to CPU when GPU0 is busy.
Symptom: planner-qwen runs on CPU at ~3 tokens/s, blowing every helper
timeout. Root cause is filesystem state (Startup\\Ollama.lnk), but a
gateway probe is the durable second line of defense — it tells the
operator at boot time that Ollama is misconfigured rather than letting
the first user turn wait 90s for a CPU-bound synth.

These tests pin probe behavior:
  * 100% GPU → ok (size_vram == size)
  * 100% CPU → fail (size_vram == 0)
  * partial GPU offload → fail (size_vram < size)
  * model not in /api/ps → retries, then fail
  * network error / 5xx → fail with reason
  * model name match is case-insensitive prefix (planner-qwen matches
    `planner-qwen:latest`)
"""
from __future__ import annotations

import json

import httpx
import pytest

from gateway.ollama_probe import ProbeResult, check_model_on_gpu


def _ps_response(*models: dict) -> httpx.Response:
    return httpx.Response(200, json={"models": list(models)})


def _model_entry(
    name: str = "planner-qwen:latest",
    size: int = 11_000_000_000,
    size_vram: int | None = None,
) -> dict:
    if size_vram is None:
        size_vram = size
    return {
        "name": name,
        "model": name,
        "size": size,
        "size_vram": size_vram,
        "details": {"family": "qwen3"},
    }


def _transport(*responses: httpx.Response) -> httpx.MockTransport:
    """Build a transport that returns each response in order then loops."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if not queue:
            return responses[-1]
        return queue.pop(0)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_full_gpu_passes() -> None:
    transport = _transport(_ps_response(_model_entry()))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1,
        )
    assert isinstance(res, ProbeResult)
    assert res.ok is True
    assert res.processor == "gpu"
    assert res.gpu_pct == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_full_cpu_fails() -> None:
    transport = _transport(_ps_response(_model_entry(size_vram=0)))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1,
        )
    assert res.ok is False
    assert res.processor == "cpu"
    assert res.gpu_pct == pytest.approx(0.0)
    assert "CPU" in res.message


@pytest.mark.asyncio
async def test_partial_offload_fails() -> None:
    transport = _transport(_ps_response(
        _model_entry(size=10_000_000_000, size_vram=4_000_000_000),
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1,
        )
    assert res.ok is False
    assert res.processor == "mixed"
    assert 0 < res.gpu_pct < 100


@pytest.mark.asyncio
async def test_missing_model_retries_then_fails() -> None:
    empty = _ps_response()
    transport = _transport(empty, empty, empty)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=3, retry_delay=0.0,
        )
    assert res.ok is False
    assert res.processor == "missing"
    assert "planner-qwen" in res.message


@pytest.mark.asyncio
async def test_missing_then_appears_passes() -> None:
    transport = _transport(
        _ps_response(),
        _ps_response(_model_entry()),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=3, retry_delay=0.0,
        )
    assert res.ok is True
    assert res.processor == "gpu"


@pytest.mark.asyncio
async def test_network_error_fails() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ollama not reachable")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1, retry_delay=0.0,
        )
    assert res.ok is False
    assert res.processor == "unreachable"
    assert "ollama" in res.message.lower()


@pytest.mark.asyncio
async def test_5xx_fails() -> None:
    transport = _transport(httpx.Response(503, text="overloaded"))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1, retry_delay=0.0,
        )
    assert res.ok is False
    assert res.processor == "unreachable"


@pytest.mark.asyncio
async def test_model_name_prefix_match() -> None:
    """Prefix match: `planner-qwen` matches `planner-qwen:latest`."""
    transport = _transport(_ps_response(
        _model_entry(name="planner-qwen:latest"),
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1,
        )
    assert res.ok is True


@pytest.mark.asyncio
async def test_other_models_ignored() -> None:
    """Probe doesn't care if other models are on CPU."""
    transport = _transport(_ps_response(
        _model_entry(name="nomic-embed-text:latest", size_vram=0),
        _model_entry(name="planner-qwen:latest"),
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1,
        )
    assert res.ok is True


@pytest.mark.asyncio
async def test_malformed_json_fails() -> None:
    transport = _transport(httpx.Response(200, text="not json"))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await check_model_on_gpu(
            "planner-qwen", client=client, retries=1, retry_delay=0.0,
        )
    assert res.ok is False
    assert res.processor == "unreachable"
