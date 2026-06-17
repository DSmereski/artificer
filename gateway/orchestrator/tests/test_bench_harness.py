"""Tests for the bench harness — orchestrates corpus + runtime + scorer
to produce a BenchScore per (role, model) pair."""
from __future__ import annotations
from unittest.mock import AsyncMock

import pytest

from gateway.orchestrator.bench_corpus import BenchCase
from gateway.orchestrator.bench_harness import bench_role_against_model
from gateway.orchestrator.runtimes.ollama_runtime import BenchInvocation


@pytest.mark.asyncio
async def test_bench_role_against_model_returns_score():
    cases = [
        BenchCase(id="cr1", prompt="...", expected_keywords=("kraken",), max_tokens=100),
        BenchCase(id="cr2", prompt="...", expected_keywords=("thread",), max_tokens=100),
    ]

    fake_invocations = [
        BenchInvocation(output="the kraken sleeps", token_count=4, latency_ms=500.0),
        BenchInvocation(output="the thread is hot", token_count=5, latency_ms=700.0),
    ]
    invoker = AsyncMock(side_effect=fake_invocations)

    score = await bench_role_against_model(
        cases=cases,
        invoker=invoker,
        model_id="planner-qwen",
        cost_per_1k_tokens=0.0,
    )

    assert score.latency_p50_ms == 600.0  # median of 500, 700
    assert score.tokens_per_s > 0
    assert 0.5 <= score.quality_score <= 1.0
    assert score.cost_per_1k_tokens == 0.0
    assert score.last_run_at > 0


@pytest.mark.asyncio
async def test_bench_role_against_model_empty_cases_raises():
    invoker = AsyncMock()
    with pytest.raises(ValueError, match="non-empty"):
        await bench_role_against_model(
            cases=[],
            invoker=invoker,
            model_id="planner-qwen",
            cost_per_1k_tokens=0.0,
        )


@pytest.mark.asyncio
async def test_build_invoker_cpu_only_model_forces_num_gpu_zero(monkeypatch):
    """A model with gpu_vram_mb=0 (registered CPU-only) must bench on CPU.
    The bench harness's internal invoker has to forward num_gpu=0 to
    invoke_ollama or the bench numbers are GPU-path measurements that
    don't reflect what the production helper will see.
    """
    from gateway.orchestrator import bench_harness as bh
    from gateway.model_catalog import ModelEntry

    captured: dict = {}

    async def fake_invoke_ollama(**kwargs):
        captured.update(kwargs)
        return BenchInvocation(output="ok", token_count=1, latency_ms=10.0)

    monkeypatch.setattr(bh, "invoke_ollama", fake_invoke_ollama)

    cpu_model = ModelEntry(
        id="gemma3-4b",
        ollama_name="gemma3:4b",
        family="gemma3",
        gpu_vram_mb=0,
        cpu_ram_mb=6000,
        cpu_fallback=True,
        speciality="cpu researcher",
        use_for=("researcher",),
    )
    invoker = bh._build_invoker(
        model=cpu_model,
        ollama_host_url="http://localhost:11434",
        anthropic_api_key=None,
    )
    assert invoker is not None
    await invoker(BenchCase(id="x", prompt="p", expected_keywords=(), max_tokens=50))
    assert captured["num_gpu"] == 0


@pytest.mark.asyncio
async def test_build_invoker_gpu_model_omits_num_gpu(monkeypatch):
    """GPU-resident models leave num_gpu unset so Ollama is free to
    place them however it sees fit (production GPU path)."""
    from gateway.orchestrator import bench_harness as bh
    from gateway.model_catalog import ModelEntry

    captured: dict = {}

    async def fake_invoke_ollama(**kwargs):
        captured.update(kwargs)
        return BenchInvocation(output="ok", token_count=1, latency_ms=10.0)

    monkeypatch.setattr(bh, "invoke_ollama", fake_invoke_ollama)

    gpu_model = ModelEntry(
        id="planner-qwen",
        ollama_name="planner-qwen",
        family="qwen2.5",
        gpu_vram_mb=9500,
        cpu_ram_mb=11000,
        cpu_fallback=True,
        speciality="x",
        use_for=("researcher",),
    )
    invoker = bh._build_invoker(
        model=gpu_model,
        ollama_host_url="http://localhost:11434",
        anthropic_api_key=None,
    )
    assert invoker is not None
    await invoker(BenchCase(id="x", prompt="p", expected_keywords=(), max_tokens=50))
    assert captured.get("num_gpu") is None


@pytest.mark.asyncio
async def test_bench_role_against_model_zero_quality_when_no_keyword_match():
    cases = [
        BenchCase(id="x", prompt="...", expected_keywords=("kraken",), max_tokens=50),
    ]
    fake_invocations = [
        BenchInvocation(output="penguins are nice", token_count=3, latency_ms=300.0),
    ]
    invoker = AsyncMock(side_effect=fake_invocations)

    score = await bench_role_against_model(
        cases=cases,
        invoker=invoker,
        model_id="planner-qwen",
        cost_per_1k_tokens=0.0,
    )
    assert score.quality_score == 0.0
