"""Phase C.2 of #476: confirm gemma3-ablit-4b is routable for the
researcher role from the real catalog + bench results.

Phase A added gemma3-ablit-4b to the researcher candidate list and the
bench harness recorded scores for both planner-qwen and gemma3-ablit-4b.
This test guarantees both still appear and Router.route_for picks one
of them deterministically (no fallback to YAML default)."""
from __future__ import annotations

from pathlib import Path

import pytest

from gateway.model_catalog import load_catalog
from gateway.orchestrator.bench_results import load_results
from gateway.orchestrator.router import Router


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CATALOG = _PROJECT_ROOT / "config" / "model_catalog.yaml"
_BENCH = _PROJECT_ROOT / "state" / "bench_results.json"


def _researcher_candidate_ids() -> set[str]:
    cat = load_catalog(_CATALOG)
    return {m.id for m in cat.candidates_for_role("researcher")}


def test_researcher_candidates_include_hive_qwen_and_gemma():
    ids = _researcher_candidate_ids()
    assert "planner-qwen" in ids, f"planner-qwen missing from {ids}"
    assert "gemma3-ablit-4b" in ids, f"gemma3-ablit-4b missing from {ids}"


@pytest.mark.skipif(
    not _BENCH.exists(),
    reason="bench results not yet captured on this host",
)
def test_router_route_for_researcher_returns_a_real_candidate():
    cat = load_catalog(_CATALOG)
    results = load_results(_BENCH)
    router = Router(catalog=cat, results=results)
    choice = router.route_for("researcher")
    assert choice.model.id in {"planner-qwen", "gemma3-ablit-4b"}, (
        f"router picked unexpected model {choice.model.id!r} "
        f"with reason {choice.reason!r}"
    )
    # Bench data is present for both — must NOT be the YAML-fallback path.
    assert "fallback" not in choice.reason
