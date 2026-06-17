"""Heuristic quality scoring for bench output.

This is deliberately simple — Phase 1 ships a keyword-coverage scorer.
A future task can swap in LLM-as-judge for richer scoring without
changing the Router contract (it just consumes a 0..1 float).
"""
from __future__ import annotations

from gateway.orchestrator.bench_corpus import BenchCase


def score_output(case: BenchCase, output: str) -> float:
    """Return a quality score in [0.0, 1.0].

    Rules:
    - If ``case.expected_keywords`` is non-empty: 0.0 if no keyword
      matched (case-insensitive substring); otherwise
      ``0.5 + 0.5 * coverage`` so one hit floors at 0.5 and full
      coverage scores 1.0.
    - If empty: length-based heuristic (200+ chars saturates at 1.0).
      This is a placeholder for cases without ground-truth keywords.
    """
    out_lower = output.lower()
    if case.expected_keywords:
        hits = sum(
            1 for kw in case.expected_keywords
            if kw.lower() in out_lower
        )
        if hits == 0:
            return 0.0
        coverage = hits / len(case.expected_keywords)
        return 0.5 + 0.5 * coverage
    n = len(output.strip())
    return min(1.0, n / 200.0)
