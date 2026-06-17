"""Tests for the heuristic quality scorer."""
from __future__ import annotations

from gateway.orchestrator.bench_corpus import BenchCase
from gateway.orchestrator.quality_scorer import score_output


def test_score_full_keyword_hit_returns_high():
    case = BenchCase(id="x", prompt="...", expected_keywords=("kraken", "deep"))
    score = score_output(case, output="the kraken lurks in the deep ocean")
    assert score >= 0.9


def test_score_partial_keyword_hit_proportional():
    case = BenchCase(id="x", prompt="...", expected_keywords=("kraken", "deep"))
    score = score_output(case, output="the kraken is awake")
    assert 0.6 < score < 0.85  # one of two = 0.5 + 0.5*0.5 = 0.75


def test_score_no_keywords_returns_zero():
    case = BenchCase(id="x", prompt="...", expected_keywords=("kraken",))
    score = score_output(case, output="penguins are nice")
    assert score == 0.0


def test_score_no_expected_keywords_falls_back_to_length():
    case = BenchCase(id="x", prompt="...", expected_keywords=())
    short = score_output(case, output="ok")
    long_ = score_output(case, output="this is a much longer response with substance")
    assert long_ > short
    assert 0.0 <= short <= 1.0
    assert 0.0 <= long_ <= 1.0


def test_score_case_insensitive():
    case = BenchCase(id="x", prompt="...", expected_keywords=("KRAKEN",))
    score = score_output(case, output="the kraken sleeps")
    assert score >= 0.9
