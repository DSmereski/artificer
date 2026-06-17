"""Tests for gateway.helpers.comm_graph (Phase D declarative routing)."""

from __future__ import annotations

from gateway.helpers.comm_graph import ALLOWED_EDGES, is_allowed


def test_planner_allowed_to_call_researcher():
    assert is_allowed("planner", "researcher")


def test_planner_allowed_to_call_librarian():
    assert is_allowed("planner", "librarian")


def test_planner_blocked_from_synthesizer():
    # Synthesizer is invoked directly by the coordinator post-helpers,
    # not delegated through planner. A prompt-injected note can't ride
    # planner output to spin it up early.
    assert not is_allowed("planner", "synthesizer")


def test_planner_blocked_from_critic():
    # Critic is the gate; planner cannot call it directly.
    assert not is_allowed("planner", "critic")


def test_planner_blocked_from_planner():
    # No recursion.
    assert not is_allowed("planner", "planner")


def test_unknown_caller_blocked():
    assert not is_allowed("nobody", "researcher")


def test_only_planner_is_a_caller():
    # Helpers don't call helpers in this codebase. If you change that,
    # update the registry.
    assert set(ALLOWED_EDGES.keys()) == {"planner"}


def test_allowed_edges_immutable_frozenset():
    # Each entry must be a frozenset so callers can't mutate the
    # registry by accident.
    for v in ALLOWED_EDGES.values():
        assert isinstance(v, frozenset)
