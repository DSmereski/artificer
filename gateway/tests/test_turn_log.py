"""Tests for the M6.3 structured turn log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gateway.helpers.base import HelperResult
from gateway.turn_log import (
    HelperLogEntry, TurnLogEntry, TurnLogStore, _preview, _scrub,
    helper_entries_from_results,
)


# ---------------------------------------------------------------- preview / scrub


def test_preview_short_passthrough():
    assert _preview("hi") == "hi"


def test_preview_truncates_long():
    s = "x" * 1000
    out = _preview(s)
    assert out.endswith("chars]")
    assert len(out) < 600


def test_preview_redacts_secrets():
    """Anything resembling a token / credential gets redacted."""
    secret = "ghp_" + "a" * 36
    out = _preview(f"my key is {secret} ok?")
    assert secret not in out
    assert "REDACTED" in out


def test_scrub_with_limit():
    s = "x" * 100
    out = _scrub(s, limit=20)
    assert len(out) == 20


def test_scrub_handles_none():
    assert _scrub(None) == ""


# ---------------------------------------------------------------- store


@pytest.fixture
def store(tmp_path):
    return TurnLogStore(tmp_path / "turn-logs", mem_cap=10)


def _entry(turn_id: str = "tk-1") -> TurnLogEntry:
    return TurnLogEntry(
        turn_id=turn_id, device_id="dev1", user_id=1, bot="hive",
        user_msg="hi", planner_summary="greet",
        delegations=[], helpers=[], final_reply="hello",
    )


def test_store_appends_to_jsonl(store, tmp_path):
    store.append(_entry("a"))
    store.append(_entry("b"))
    files = list((tmp_path / "turn-logs").glob("*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["turn_id"] == "a"
    assert json.loads(lines[1])["turn_id"] == "b"


def test_store_tail_returns_recent(store):
    for i in range(5):
        store.append(_entry(f"t{i}"))
    out = store.tail(n=3)
    assert [e["turn_id"] for e in out] == ["t2", "t3", "t4"]


def test_store_tail_more_than_buffer(store):
    for i in range(20):
        store.append(_entry(f"t{i}"))
    out = store.tail(n=100)
    # mem_cap=10 so we only retain last 10.
    assert len(out) == 10
    assert out[-1]["turn_id"] == "t19"


def test_store_files_lists_by_date(store):
    store.append(_entry())
    files = store.files()
    assert len(files) == 1


def test_store_to_jsonable_includes_helpers(store):
    helper = HelperResult(
        role="researcher", model_id="planner-qwen",
        output={"summary": "found 3 facts"},
        tokens_in=100, tokens_out=50, latency_ms=1234,
    )
    helpers = helper_entries_from_results([helper])
    store.append(TurnLogEntry(
        turn_id="t1", device_id="d", user_id=0, bot="hive",
        user_msg="research X", helpers=helpers,
    ))
    out = store.tail(1)[0]
    assert out["helpers"][0]["role"] == "researcher"
    assert out["helpers"][0]["latency_ms"] == 1234


def test_helper_entries_skip_none():
    out = helper_entries_from_results([None, None])
    assert out == []


def test_helper_entries_capture_error():
    h = HelperResult(role="x", model_id="m", error="boom")
    [entry] = helper_entries_from_results([h])
    assert entry.error == "boom"
