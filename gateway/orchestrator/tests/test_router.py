"""Tests for the orchestrator Router."""
from __future__ import annotations
from pathlib import Path
from textwrap import dedent

import pytest

from gateway.model_catalog import ModelCatalog, load_catalog
from gateway.orchestrator.bench_results import BenchResults, BenchScore
from gateway.orchestrator.router import ModelChoice, Router


@pytest.fixture(autouse=True)
def _set_anthropic_key(monkeypatch):
    """Ensure cloud models are not skipped due to missing creds in these tests.

    These tests exercise routing logic, not credential checking.  Setting a
    dummy key lets cloud-model candidates pass the _has_creds() guard.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(body, encoding="utf-8")
    return p


_TWO_MODEL_YAML = dedent("""\
    models:
      - id: planner-qwen
        ollama_name: planner-qwen
        family: qwen2.5
        gpu_vram_mb: 9500
        cpu_ram_mb: 11000
        cpu_fallback: true
        speciality: x
        use_for: [chat_recall]
        params: {}
      - id: claude-haiku-4-5-20251001
        cloud_provider: anthropic
        cloud_model_name: claude-haiku-4-5-20251001
        cost_per_1k_tokens_input: 0.0008
        cost_per_1k_tokens_output: 0.004
        speciality: cloud
        use_for: [chat_recall]
        params: {}
    helpers:
      - role: chat_recall
        model: planner-qwen
        candidates: [planner-qwen, claude-haiku-4-5-20251001]
        system_prompt_file: prompts/x.md
        output_schema: X
        timeout_s: 10
""")


@pytest.fixture
def two_model_catalog(tmp_path: Path) -> ModelCatalog:
    return load_catalog(_write_yaml(tmp_path, _TWO_MODEL_YAML))


def _score(latency=1000.0, tokens_per_s=50.0, quality=0.7, cost=0.0):
    return BenchScore(
        latency_p50_ms=latency, tokens_per_s=tokens_per_s,
        quality_score=quality, cost_per_1k_tokens=cost, last_run_at=0.0,
    )


def test_router_falls_back_to_default_when_no_bench_data(two_model_catalog):
    router = Router(catalog=two_model_catalog, results=BenchResults())
    choice = router.route_for("chat_recall")
    assert isinstance(choice, ModelChoice)
    assert choice.model.id == "planner-qwen"  # default from helpers.model
    assert "no-bench" in choice.reason


def test_router_picks_higher_score(two_model_catalog):
    results = BenchResults(scores={
        "chat_recall": {
            "planner-qwen": _score(latency=2000, tokens_per_s=20, quality=0.6),
            "claude-haiku-4-5-20251001": _score(
                latency=500, tokens_per_s=120, quality=0.9, cost=0.0008,
            ),
        },
    })
    router = Router(catalog=two_model_catalog, results=results)
    choice = router.route_for("chat_recall")
    assert choice.model.id == "claude-haiku-4-5-20251001"
    assert choice.reason.startswith("score=")


def test_router_prefers_free_when_quality_close(two_model_catalog):
    """Free model wins when quality + latency are roughly equal."""
    results = BenchResults(scores={
        "chat_recall": {
            "planner-qwen": _score(latency=500, tokens_per_s=80, quality=0.85),
            "claude-haiku-4-5-20251001": _score(
                latency=500, tokens_per_s=120, quality=0.85, cost=0.01,
            ),
        },
    })
    router = Router(catalog=two_model_catalog, results=results)
    choice = router.route_for("chat_recall")
    # Same q + same lat, but cost weight pushes planner-qwen ahead.
    assert choice.model.id == "planner-qwen"


def test_router_unknown_role_raises(two_model_catalog):
    router = Router(catalog=two_model_catalog, results=BenchResults())
    with pytest.raises(KeyError):
        router.route_for("no_such_role")


def test_router_skips_candidate_without_bench_data(two_model_catalog):
    """If only one of two candidates has been benched, route to it."""
    results = BenchResults(scores={
        "chat_recall": {
            "claude-haiku-4-5-20251001": _score(quality=0.9, cost=0.001),
        },
    })
    router = Router(catalog=two_model_catalog, results=results)
    choice = router.route_for("chat_recall")
    assert choice.model.id == "claude-haiku-4-5-20251001"
