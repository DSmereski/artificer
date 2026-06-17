"""Tests for the helper factory: model + prompt + schema wiring."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from gateway.helpers.factory import build_helpers
from gateway.model_catalog import load_catalog


@pytest.fixture
def catalog():
    return load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )


def test_factory_builds_every_role(catalog):
    pool = build_helpers(catalog)
    expected = {
        "planner", "coder", "researcher", "image_director",
        "sysmon", "summarizer", "critic", "librarian",
        "synthesizer", "skill_runner",
    }
    assert expected.issubset(set(pool.keys()))


def test_factory_passes_helper_params_override(tmp_path):
    """Helper-level `params` override the model-level params."""
    yaml_body = dedent("""\
        models:
          - id: m
            ollama_name: planner-qwen
            family: qwen
            gpu_vram_mb: 1
            cpu_ram_mb: 1
            cpu_fallback: true
            speciality: x
            use_for: [planner]
            params:
              temperature: 0.7
              num_predict: 1024

        helpers:
          - role: planner
            model: m
            system_prompt_file: prompts/planner.md
            output_schema: HelperPlan
            timeout_s: 60
            params:
              temperature: 0.2
              num_predict: 2048
    """)
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml_body, encoding="utf-8")
    cat = load_catalog(p)
    pool = build_helpers(cat)
    helper = pool["planner"]
    # Helper's `params` win.
    assert helper.params["temperature"] == 0.2
    assert helper.params["num_predict"] == 2048


def test_factory_skill_registry_passthrough(tmp_path):
    """When a SkillRegistry is provided, the skill_runner helper
    receives it via `registry=`."""
    from gateway.skill_registry import SkillRegistry

    cat = load_catalog(
        Path(__file__).resolve().parents[2] / "config" / "model_catalog.yaml",
    )
    reg = SkillRegistry(tmp_path / "skills")
    reg.load()
    pool = build_helpers(cat, skill_registry=reg)
    runner = pool.get("skill_runner")
    assert runner is not None
    # The registry got attached.
    assert runner._registry is reg


def test_factory_models_share_terry_qwen(catalog):
    """All helpers in the production catalog share planner-qwen."""
    pool = build_helpers(catalog)
    for role, h in pool.items():
        if role == "vault_search":
            continue       # nomic-embed only
        assert h.model_id == "planner-qwen", (
            f"{role!r} should use planner-qwen but uses {h.model_id!r}"
        )


# ---------------------------------------------------------------- plan §3.1

def test_factory_uses_router_pick_over_catalog_default(catalog):
    """build_helpers consults the router and bakes its per-role pick
    into the helper. Without a router, the catalog YAML default wins."""
    class _FakeRouter:
        def __init__(self, picks):
            self._picks = picks

        def route_for(self, role):
            from gateway.orchestrator.router import ModelChoice
            entry = self._picks.get(role)
            if entry is None:
                raise KeyError(role)
            return ModelChoice(model=entry, reason="forced for test")

    from gateway.helpers.factory import build_helpers
    default_pool = build_helpers(catalog)
    default_planner_model = default_pool["planner"].model_id

    # Pick a different known model from the same catalog for planner;
    # leave summarizer unrouted so it falls back to the YAML default.
    other_model_id = next(
        mid for mid in catalog.model_ids if mid != default_planner_model
    )
    other_entry = catalog.model(other_model_id)
    router = _FakeRouter({"planner": other_entry})

    routed_pool = build_helpers(catalog, router=router)
    assert routed_pool["planner"].model_id == other_model_id
    # Summarizer: no router pick → unchanged.
    assert routed_pool["summarizer"].model_id == default_pool["summarizer"].model_id
