"""Tests for P7 board-stats surfacing: bench scores, loop decisions, goal cycles.

These test the helper functions that populate the /board/stats payload with
data from the bench harness, loop-refinement decisions, and goal-loop records.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway.routes.board import (
    _bench_model_scores,
    _loop_decisions,
    _goal_cycle_stats,
    _BENCH_RESULTS_PATH,
    _LOOP_DECISIONS_PATH,
)
from gateway.crew_board.store import CrewBoardStore
from gateway.crew_board.goal_loop import GoalRecord, store_goal


# ── bench scores ──────────────────────────────────────────────────────────

def test_bench_scores_reads_results_file(tmp_path):
    """_bench_model_scores loads bench_results.json and returns per-role rows."""
    results = {
        "scores": {
            "coder": {
                "planner-qwen": {
                    "latency_p50_ms": 400.0,
                    "tokens_per_s": 30.0,
                    "quality_score": 0.85,
                    "cost_per_1k_tokens": 0.0,
                    "last_run_at": 0.0,
                },
            },
        },
    }
    bf = tmp_path / "bench_results.json"
    bf.write_text(json.dumps(results), encoding="utf-8")
    with patch("gateway.routes.board._BENCH_RESULTS_PATH", bf):
        rows = _bench_model_scores()
    assert len(rows) == 1
    assert rows[0]["role"] == "coder"
    assert rows[0]["model"] == "planner-qwen"
    assert rows[0]["quality"] == 0.85
    # Composite: 0.5*0.85 + 0.3*min(500/400,1) + 0.2*1.0 = 0.425 + 0.3 + 0.2 = 0.925
    assert rows[0]["composite"] == 0.925


def test_bench_scores_returns_empty_on_missing_file():
    """Missing bench_results.json → empty list, no crash."""
    with patch("gateway.routes.board._BENCH_RESULTS_PATH", Path("/nonexistent/nope.json")):
        assert _bench_model_scores() == []


# ── loop decisions ────────────────────────────────────────────────────────

def test_loop_decisions_reads_file(tmp_path):
    """_loop_decisions loads loop_decisions.json and returns per-role rows."""
    decisions = {
        "coder": {
            "planner-qwen": {
                "adopt": True,
                "delta": 0.12,
                "single_mean": 0.7,
                "loop_mean": 0.82,
                "ts": "2026-06-20T00:00:00",
            },
        },
    }
    lf = tmp_path / "loop_decisions.json"
    lf.write_text(json.dumps(decisions), encoding="utf-8")
    with patch("gateway.routes.board._LOOP_DECISIONS_PATH", lf):
        rows = _loop_decisions()
    assert len(rows) == 1
    assert rows[0]["role"] == "coder"
    assert rows[0]["adopt"] is True
    assert rows[0]["delta"] == 0.12


def test_loop_decisions_returns_empty_on_missing():
    """Missing loop_decisions.json → empty list, no crash."""
    with patch("gateway.routes.board._LOOP_DECISIONS_PATH", Path("/nonexistent/nope.json")):
        assert _loop_decisions() == []


# ── goal cycle stats ──────────────────────────────────────────────────────

def test_goal_cycle_stats_summarizes_goals(tmp_path):
    """_goal_cycle_stats reads goal records from crew_meta via list_meta_like."""
    db = tmp_path / "crew.db"
    store = CrewBoardStore(db)
    # Create a few goal records.
    g1 = GoalRecord(
        goal_id="g1", text="Build feature X", project_slug="proj-a",
        checklist=[{"item": "tests pass", "met": True}, {"item": "docs updated", "met": False}],
        cycle=1, status="active",
    )
    g2 = GoalRecord(
        goal_id="g2", text="Fix bug Y", project_slug="proj-a",
        checklist=[{"item": "crash gone", "met": True}],
        cycle=0, status="complete",
    )
    g3 = GoalRecord(
        goal_id="g3", text="Stuck goal", project_slug="proj-b",
        checklist=[{"item": "never works", "met": False}],
        cycle=3, status="needs_you",
    )
    store_goal(store, g1)
    store_goal(store, g2)
    store_goal(store, g3)

    result = _goal_cycle_stats(store)
    assert result["active"] == 1
    assert result["complete"] == 1
    assert result["needs_you"] == 1
    assert result["total"] == 3
    assert result["max_cycle"] == 3
    assert len(result["goals"]) == 3
    # Check one goal's checklist summary.
    g1_row = next(g for g in result["goals"] if g["goal_id"] == "g1")
    assert g1_row["checklist_met"] == 1
    assert g1_row["checklist_total"] == 2


def test_goal_cycle_stats_empty_store(tmp_path):
    """Empty store → zero counts, no crash."""
    db = tmp_path / "crew.db"
    store = CrewBoardStore(db)
    result = _goal_cycle_stats(store)
    assert result["total"] == 0
    assert result["goals"] == []
