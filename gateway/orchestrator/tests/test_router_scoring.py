"""P1 probe: composite scoring router — table-driven tests.

Covers:
  - Seeded bench rows → router picks the expected model per role.
  - Role with no bench data → falls back to model_catalog.yaml default.
  - Cloud model with missing creds is skipped gracefully.
  - Score formula: 0.5*quality + 0.3*min(500/lat,1) + 0.2*min(0.001/cost,1).
  - Tiebreaker: lower cost_per_1k wins.

These tests are intentionally independent of the live bench_results.json
or any Ollama/Anthropic endpoint.  All BenchResults are constructed inline.
"""
from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest

from gateway.model_catalog import ModelCatalog, load_catalog
from gateway.orchestrator.bench_results import BenchResults, BenchScore
from gateway.orchestrator.router import Router, _has_creds


# ---------------------------------------------------------------------------
# Shared YAML fixture helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _score(
    *,
    latency: float = 1000.0,
    tokens_per_s: float = 50.0,
    quality: float = 0.7,
    cost: float = 0.0,
) -> BenchScore:
    return BenchScore(
        latency_p50_ms=latency,
        tokens_per_s=tokens_per_s,
        quality_score=quality,
        cost_per_1k_tokens=cost,
        last_run_at=0.0,
    )


# ---------------------------------------------------------------------------
# Catalog fixtures
# ---------------------------------------------------------------------------

_THREE_MODEL_YAML = dedent("""\
    models:
      - id: local-fast
        ollama_name: local-fast
        family: qwen2.5
        gpu_vram_mb: 4000
        cpu_fallback: false
        speciality: fast-local
        use_for: [coding]
        params: {}
      - id: local-slow
        ollama_name: local-slow
        family: qwen2.5
        gpu_vram_mb: 10000
        cpu_fallback: false
        speciality: quality-local
        use_for: [coding]
        params: {}
      - id: cloud-model
        cloud_provider: anthropic
        cloud_model_name: claude-test-model
        cost_per_1k_tokens_input: 0.002
        cost_per_1k_tokens_output: 0.01
        speciality: cloud
        use_for: [coding]
        params: {}
    helpers:
      - role: coding
        model: local-fast
        candidates: [local-fast, local-slow, cloud-model]
        system_prompt_file: prompts/x.md
        output_schema: X
        timeout_s: 10
""")

_SINGLE_MODEL_YAML = dedent("""\
    models:
      - id: only-model
        ollama_name: only-model
        family: llama3
        gpu_vram_mb: 3000
        cpu_fallback: true
        speciality: only
        use_for: [summarize]
        params: {}
    helpers:
      - role: summarize
        model: only-model
        candidates: [only-model]
        system_prompt_file: prompts/x.md
        output_schema: X
        timeout_s: 10
""")


@pytest.fixture
def three_model_catalog(tmp_path: Path) -> ModelCatalog:
    return load_catalog(_write_yaml(tmp_path, _THREE_MODEL_YAML))


@pytest.fixture
def single_model_catalog(tmp_path: Path) -> ModelCatalog:
    return load_catalog(_write_yaml(tmp_path, _SINGLE_MODEL_YAML))


# ---------------------------------------------------------------------------
# Composite score formula unit test
# ---------------------------------------------------------------------------

class TestCompositeFormula:
    """Verify _composite produces correct values per the spec formula."""

    def _make_router(self, tmp_path: Path) -> Router:
        cat = load_catalog(_write_yaml(tmp_path, _THREE_MODEL_YAML))
        return Router(catalog=cat, results=BenchResults())

    def test_perfect_model_scores_1(self, tmp_path: Path) -> None:
        router = self._make_router(tmp_path)
        s = _score(quality=1.0, latency=500.0, cost=0.001)
        # lat_norm = min(500/500, 1) = 1.0; cost_norm = min(0.001/0.001,1) = 1.0
        assert router._composite(s) == pytest.approx(1.0)

    def test_zero_cost_model_gets_full_cost_term(self, tmp_path: Path) -> None:
        router = self._make_router(tmp_path)
        # cost_per_1k_tokens == 0 → cost_norm = 1.0 (free model)
        s = _score(quality=1.0, latency=500.0, cost=0.0)
        assert router._composite(s) == pytest.approx(1.0)

    def test_slow_model_capped_latency(self, tmp_path: Path) -> None:
        router = self._make_router(tmp_path)
        # latency >> LATENCY_ANCHOR_MS → latency_norm capped at 1.0 …
        # wait, fast = higher score: norm_latency = min(500/lat, 1)
        # very slow (lat=50_000ms): norm = min(500/50000,1) = 0.01
        s = _score(quality=0.8, latency=50_000.0, cost=0.001)
        expected = 0.5 * 0.8 + 0.3 * min(500 / 50_000, 1.0) + 0.2 * 1.0
        assert router._composite(s) == pytest.approx(expected)

    def test_expensive_model_penalized(self, tmp_path: Path) -> None:
        router = self._make_router(tmp_path)
        s = _score(quality=0.9, latency=300.0, cost=0.1)  # very expensive
        lat_norm = min(500 / 300, 1.0)
        cost_norm = min(0.001 / 0.1, 1.0)
        expected = 0.5 * 0.9 + 0.3 * lat_norm + 0.2 * cost_norm
        assert router._composite(s) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Table-driven routing decision tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scores,expected_id,label", [
    (
        # Local-slow has high quality + low latency → wins over local-fast
        {
            "local-fast": _score(quality=0.6, latency=300.0, cost=0.0),
            "local-slow": _score(quality=0.95, latency=400.0, cost=0.0),
        },
        "local-slow",
        "quality-dominant: local-slow beats local-fast",
    ),
    (
        # local-fast is free + fast enough to beat cloud on cost+latency
        # even with slightly lower quality (0.80 vs 0.81)
        {
            "local-fast": _score(quality=0.80, latency=300.0, cost=0.0),
            "local-slow": _score(quality=0.81, latency=600.0, cost=0.0),
        },
        "local-fast",
        "fast+free narrowly beats slow with tiny quality edge",
    ),
    (
        # Only one candidate has bench data → it wins
        {
            "local-slow": _score(quality=0.75, latency=700.0, cost=0.0),
        },
        "local-slow",
        "single-candidate with bench data wins",
    ),
])
def test_router_picks_expected_model(
    three_model_catalog: ModelCatalog,
    scores: dict[str, BenchScore],
    expected_id: str,
    label: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeded bench rows → router picks the expected model."""
    # Ensure ANTHROPIC_API_KEY is set so cloud-model is not dropped for missing creds.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    results = BenchResults(scores={"coding": scores})
    router = Router(catalog=three_model_catalog, results=results)
    choice = router.route_for("coding")
    assert choice.model.id == expected_id, (
        f"[{label}] expected {expected_id!r}, got {choice.model.id!r} "
        f"(reason: {choice.reason!r})"
    )
    assert "score=" in choice.reason, f"[{label}] reason should contain score"


# ---------------------------------------------------------------------------
# No-bench-data fallback
# ---------------------------------------------------------------------------

def test_no_bench_data_falls_back_to_yaml_default(
    three_model_catalog: ModelCatalog,
) -> None:
    """When no candidate has bench data, router uses the YAML default model."""
    router = Router(catalog=three_model_catalog, results=BenchResults())
    choice = router.route_for("coding")
    # YAML says helpers.model = local-fast
    assert choice.model.id == "local-fast"
    assert "no-bench" in choice.reason
    assert "fallback" in choice.reason


def test_no_bench_data_single_model_falls_back(
    single_model_catalog: ModelCatalog,
) -> None:
    """Single-candidate role with no bench data falls back to yaml default."""
    router = Router(catalog=single_model_catalog, results=BenchResults())
    choice = router.route_for("summarize")
    assert choice.model.id == "only-model"
    assert "no-bench" in choice.reason


# ---------------------------------------------------------------------------
# Missing creds — cloud model skipped
# ---------------------------------------------------------------------------

def test_cloud_model_skipped_when_no_api_key(
    three_model_catalog: ModelCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud model with a missing API key is skipped; local winner is chosen."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    results = BenchResults(scores={
        "coding": {
            # cloud-model has the highest raw quality but no API key
            "cloud-model": _score(quality=0.99, latency=200.0, cost=0.002),
            "local-fast": _score(quality=0.80, latency=300.0, cost=0.0),
        },
    })
    router = Router(catalog=three_model_catalog, results=results)
    choice = router.route_for("coding")
    # cloud-model must be skipped; local-fast should win
    assert choice.model.id == "local-fast", (
        f"expected local-fast (cloud-model skipped), got {choice.model.id!r}"
    )


def test_cloud_model_used_when_api_key_present(
    three_model_catalog: ModelCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud model is selected when the API key env var is set.

    Score math (anchors: lat=500ms, cost=$0.001/1k):
      cloud-model: q=0.99 lat=400ms cost=0.0009 (below anchor → cost_norm=1.0)
        score = 0.5*0.99 + 0.3*min(500/400,1) + 0.2*1.0 = 0.495 + 0.3 + 0.2 = 0.995
      local-fast: q=0.60 lat=800ms cost=0.0 (free → cost_norm=1.0)
        score = 0.5*0.60 + 0.3*min(500/800,1) + 0.2*1.0 = 0.30 + 0.1875 + 0.2 = 0.6875
    cloud-model wins clearly.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    results = BenchResults(scores={
        "coding": {
            # q=0.99, fast, cheap → composite ≈ 0.995
            "cloud-model": _score(quality=0.99, latency=400.0, cost=0.0009),
            # q=0.60, slow, free → composite ≈ 0.688
            "local-fast": _score(quality=0.60, latency=800.0, cost=0.0),
        },
    })
    router = Router(catalog=three_model_catalog, results=results)
    choice = router.route_for("coding")
    assert choice.model.id == "cloud-model"


def test_cloud_model_skipped_all_no_creds_falls_back_to_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ALL candidates with bench data lack creds, fall back to yaml default."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yaml_text = dedent("""\
        models:
          - id: local-default
            ollama_name: local-default
            family: llama3
            gpu_vram_mb: 3000
            cpu_fallback: true
            speciality: default
            use_for: [qa]
            params: {}
          - id: cloud-only
            cloud_provider: anthropic
            cloud_model_name: claude-qa
            cost_per_1k_tokens_input: 0.001
            cost_per_1k_tokens_output: 0.005
            speciality: cloud
            use_for: [qa]
            params: {}
        helpers:
          - role: qa
            model: local-default
            candidates: [local-default, cloud-only]
            system_prompt_file: prompts/x.md
            output_schema: X
            timeout_s: 10
    """)
    cat = load_catalog(_write_yaml(tmp_path, yaml_text))
    # Only cloud-only has bench data, but its key is missing
    results = BenchResults(scores={
        "qa": {
            "cloud-only": _score(quality=0.95, latency=300.0, cost=0.001),
        },
    })
    router = Router(catalog=cat, results=results)
    choice = router.route_for("qa")
    # scored list is empty → yaml fallback
    assert choice.model.id == "local-default"
    assert "no-bench" in choice.reason


# ---------------------------------------------------------------------------
# _has_creds unit tests
# ---------------------------------------------------------------------------

class TestHasCreds:
    def test_local_model_always_has_creds(self, tmp_path: Path) -> None:
        cat = load_catalog(_write_yaml(tmp_path, _THREE_MODEL_YAML))
        local = cat.model("local-fast")
        assert _has_creds(local) is True

    def test_anthropic_model_no_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cat = load_catalog(_write_yaml(tmp_path, _THREE_MODEL_YAML))
        cloud = cat.model("cloud-model")
        assert _has_creds(cloud) is False

    def test_anthropic_model_with_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-valid")
        cat = load_catalog(_write_yaml(tmp_path, _THREE_MODEL_YAML))
        cloud = cat.model("cloud-model")
        assert _has_creds(cloud) is True

    def test_blank_key_treated_as_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        cat = load_catalog(_write_yaml(tmp_path, _THREE_MODEL_YAML))
        cloud = cat.model("cloud-model")
        assert _has_creds(cloud) is False
