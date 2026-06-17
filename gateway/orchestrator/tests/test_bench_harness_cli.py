"""Tests for run_full_sweep — the multi-role × multi-candidate sweep driver."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from gateway.model_catalog import load_catalog
from gateway.orchestrator.bench_harness import run_full_sweep
from gateway.orchestrator.bench_results import load_results
from gateway.orchestrator.runtimes.ollama_runtime import BenchInvocation


def _write_corpus(corpus_dir: Path, role: str, prompts: list[dict]) -> None:
    p = corpus_dir / f"{role}.jsonl"
    p.write_text("\n".join(json.dumps(d) for d in prompts) + "\n", encoding="utf-8")


def _minimal_catalog_yaml(tmp_path: Path) -> Path:
    """Two-model catalog: one ollama, one cloud (anthropic). One helper
    with both as candidates."""
    yaml_doc = {
        "models": [
            {
                "id": "test-qwen", "ollama_name": "qwen3:0.6b",
                "family": "qwen", "speciality": "test",
                "use_for": ["chat_recall"],
            },
            {
                "id": "test-claude", "cloud_provider": "anthropic",
                "cloud_model_name": "claude-haiku-4-5-20251001",
                "family": "claude", "speciality": "test cloud",
                "use_for": ["chat_recall"],
                "cost_per_1k_tokens_output": 0.005,
            },
        ],
        "helpers": [
            {
                "role": "chat_recall",
                "model": "test-qwen",
                "candidates": ["test-qwen", "test-claude"],
                "system_prompt_file": "noop.md",
                "output_schema": "dict",
                "timeout_s": 30,
            },
        ],
    }
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump(yaml_doc), encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_run_full_sweep_writes_one_score_per_candidate(tmp_path, monkeypatch):
    """Both ollama + cloud candidates score and persist."""
    corpus_dir = tmp_path / "bench_corpus"
    corpus_dir.mkdir()
    _write_corpus(corpus_dir, "chat_recall", [
        {"id": "cr1", "prompt": "What did we discuss about the kraken?",
         "expected_keywords": ["kraken"], "max_tokens": 50},
    ])

    catalog = load_catalog(_minimal_catalog_yaml(tmp_path))
    results_path = tmp_path / "bench_results.json"

    fake_ollama = AsyncMock(return_value=BenchInvocation(
        output="The kraken is a sea monster", token_count=8, latency_ms=120,
    ))
    fake_claude = AsyncMock(return_value=BenchInvocation(
        output="kraken: a legendary cephalopod", token_count=6, latency_ms=300,
    ))
    monkeypatch.setattr(
        "gateway.orchestrator.bench_harness.invoke_ollama", fake_ollama,
    )
    monkeypatch.setattr(
        "gateway.orchestrator.bench_harness.invoke_claude", fake_claude,
    )

    out = await run_full_sweep(
        catalog=catalog,
        corpus_dir=corpus_dir,
        results_path=results_path,
        ollama_host_url="http://localhost:11434",
        anthropic_api_key="sk-fake",
    )

    assert "chat_recall" in out.scores
    assert set(out.scores["chat_recall"].keys()) == {"test-qwen", "test-claude"}
    assert fake_ollama.await_count == 1
    assert fake_claude.await_count == 1

    persisted = load_results(results_path)
    assert set(persisted.scores["chat_recall"].keys()) == {"test-qwen", "test-claude"}


@pytest.mark.asyncio
async def test_run_full_sweep_skips_cloud_when_no_api_key(
    tmp_path, monkeypatch, caplog,
):
    """Cloud candidates are skipped when anthropic_api_key is None."""
    corpus_dir = tmp_path / "bench_corpus"
    corpus_dir.mkdir()
    _write_corpus(corpus_dir, "chat_recall", [
        {"id": "cr1", "prompt": "p", "expected_keywords": [], "max_tokens": 16},
    ])
    catalog = load_catalog(_minimal_catalog_yaml(tmp_path))
    results_path = tmp_path / "bench_results.json"

    fake_ollama = AsyncMock(return_value=BenchInvocation(
        output="ok", token_count=2, latency_ms=50,
    ))
    fake_claude = AsyncMock(return_value=BenchInvocation(
        output="should-not-run", token_count=2, latency_ms=50,
    ))
    monkeypatch.setattr(
        "gateway.orchestrator.bench_harness.invoke_ollama", fake_ollama,
    )
    monkeypatch.setattr(
        "gateway.orchestrator.bench_harness.invoke_claude", fake_claude,
    )

    out = await run_full_sweep(
        catalog=catalog,
        corpus_dir=corpus_dir,
        results_path=results_path,
        ollama_host_url="http://localhost:11434",
        anthropic_api_key=None,
    )

    # Only ollama candidate scored
    assert set(out.scores["chat_recall"].keys()) == {"test-qwen"}
    assert fake_ollama.await_count == 1
    assert fake_claude.await_count == 0


@pytest.mark.asyncio
async def test_run_full_sweep_merges_with_existing(tmp_path, monkeypatch):
    """Pre-existing scores for OTHER roles are preserved across sweeps."""
    corpus_dir = tmp_path / "bench_corpus"
    corpus_dir.mkdir()
    _write_corpus(corpus_dir, "chat_recall", [
        {"id": "cr1", "prompt": "p", "expected_keywords": [], "max_tokens": 16},
    ])
    catalog = load_catalog(_minimal_catalog_yaml(tmp_path))
    results_path = tmp_path / "bench_results.json"

    # Seed an unrelated role so we can detect it survives.
    from gateway.orchestrator.bench_results import (
        BenchResults, BenchScore, save_results,
    )
    seed = BenchResults(scores={
        "synthesizer": {
            "test-qwen": BenchScore(
                latency_p50_ms=100.0, tokens_per_s=10.0,
                quality_score=0.8, cost_per_1k_tokens=0.0,
                last_run_at=0.0,
            ),
        },
    })
    save_results(results_path, seed)

    fake_ollama = AsyncMock(return_value=BenchInvocation(
        output="ok", token_count=2, latency_ms=50,
    ))
    monkeypatch.setattr(
        "gateway.orchestrator.bench_harness.invoke_ollama", fake_ollama,
    )

    out = await run_full_sweep(
        catalog=catalog,
        corpus_dir=corpus_dir,
        results_path=results_path,
        ollama_host_url="http://localhost:11434",
        anthropic_api_key=None,
    )

    # New chat_recall row added, original synthesizer row preserved.
    assert "chat_recall" in out.scores
    assert "synthesizer" in out.scores
    assert "test-qwen" in out.scores["synthesizer"]
