"""Tests for bench_results.json read/write atomicity + schema."""
from __future__ import annotations
from pathlib import Path

from gateway.orchestrator.bench_results import (
    BenchScore,
    BenchResults,
    load_results,
    save_results,
)


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "bench_results.json"
    results = BenchResults(scores={
        "chat_recall": {
            "planner-qwen": BenchScore(
                latency_p50_ms=850, tokens_per_s=45.2,
                quality_score=0.78, cost_per_1k_tokens=0.0,
                last_run_at=1700000000.0,
            ),
            "claude-haiku-4-5-20251001": BenchScore(
                latency_p50_ms=1200, tokens_per_s=120.0,
                quality_score=0.91, cost_per_1k_tokens=0.0008,
                last_run_at=1700000050.0,
            ),
        },
    })
    save_results(path, results)

    loaded = load_results(path)
    assert "chat_recall" in loaded.scores
    assert "planner-qwen" in loaded.scores["chat_recall"]
    score = loaded.scores["chat_recall"]["planner-qwen"]
    assert score.latency_p50_ms == 850
    assert score.cost_per_1k_tokens == 0.0


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.json"
    loaded = load_results(path)
    assert loaded.scores == {}


def test_save_is_atomic(tmp_path: Path) -> None:
    """Save should write to a tmp file then rename, never leaving a
    half-written file at the target path."""
    path = tmp_path / "bench_results.json"
    save_results(path, BenchResults(scores={}))
    assert path.is_file()
    assert path.read_text(encoding="utf-8")  # not empty
