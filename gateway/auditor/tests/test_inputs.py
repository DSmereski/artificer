# gateway/auditor/tests/test_inputs.py
"""Tests for turn-log + memory readers."""
from __future__ import annotations

import json
import time
from pathlib import Path

from gateway.auditor.inputs import (
    load_turns_in_window,
    load_thread_memories,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_load_turns_filters_by_window(tmp_path: Path) -> None:
    log_dir = tmp_path / "turn-logs"
    now = time.time()
    inside_ts = now - 1800
    before_ts = now - 7200
    after_ts = now + 600
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    _write_jsonl(log_dir / f"{day}.jsonl", [
        {"ts": before_ts, "turn_id": "t_old", "bot": "hive"},
        {"ts": inside_ts, "turn_id": "t_inside", "bot": "hive"},
        {"ts": after_ts, "turn_id": "t_future", "bot": "hive"},
    ])
    turns = load_turns_in_window(
        log_root=log_dir,
        window_start=now - 3600,
        window_end=now,
    )
    assert [t["turn_id"] for t in turns] == ["t_inside"]


def test_load_turns_handles_missing_dir(tmp_path: Path) -> None:
    turns = load_turns_in_window(
        log_root=tmp_path / "no-such-dir",
        window_start=0.0,
        window_end=time.time(),
    )
    assert turns == []


def test_load_turns_skips_malformed_lines(tmp_path: Path) -> None:
    log_dir = tmp_path / "turn-logs"
    log_dir.mkdir()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    p = log_dir / f"{day}.jsonl"
    now = time.time()
    p.write_text(
        json.dumps({"ts": now - 60, "turn_id": "t1", "bot": "hive"}) + "\n"
        + "not json garbage\n"
        + json.dumps({"ts": now - 30, "turn_id": "t2", "bot": "hive"}) + "\n",
        encoding="utf-8",
    )
    turns = load_turns_in_window(
        log_root=log_dir,
        window_start=now - 3600,
        window_end=now,
    )
    assert [t["turn_id"] for t in turns] == ["t1", "t2"]


def test_load_thread_memories_reads_per_thread_jsons(tmp_path: Path) -> None:
    mem_dir = tmp_path / "memory" / "hive" / "42"
    mem_dir.mkdir(parents=True)
    (mem_dir / "default.memory.json").write_text(
        json.dumps({
            "mid_summary": "user likes kraken talk",
            "user_facts": ["color: red"],
            "open_tasks": [],
            "decisions": [],
        }), encoding="utf-8",
    )
    mems = load_thread_memories(memory_root=tmp_path / "memory", bot="hive")
    assert len(mems) == 1
    assert mems[0]["user_id"] == 42
    assert mems[0]["thread_id"] == "default"
    assert mems[0]["mid_summary"].startswith("user likes")


def test_load_thread_memories_handles_missing_root(tmp_path: Path) -> None:
    out = load_thread_memories(memory_root=tmp_path / "no", bot="hive")
    assert out == []
