# gateway/auditor/inputs.py
"""Pure readers for the auditor's two input surfaces.

- ``load_turns_in_window`` glob-walks ``<state_dir>/turn-logs/*.jsonl``
  and yields parsed turn dicts whose ``ts`` falls in [start, end].
  Tolerates malformed lines so a single bad row doesn't poison the run.

- ``load_thread_memories`` reads every per-thread JSON under
  ``<state_dir>/memory/<bot>/<user_id>/<thread_id>.memory.json`` so the
  auditor can correlate findings against the persisted summary slots.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("gateway.auditor.inputs")


def load_turns_in_window(
    *,
    log_root: Path,
    window_start: float,
    window_end: float,
) -> list[dict]:
    """Return turn dicts whose ``ts`` is in [window_start, window_end].

    ``log_root`` is the directory holding ``YYYY-MM-DD.jsonl`` files
    (the same path ``TurnLogStore`` writes to).
    """
    if not log_root.is_dir():
        return []
    days_to_scan: set[str] = set()
    cur = window_start
    while cur <= window_end:
        days_to_scan.add(time.strftime("%Y-%m-%d", time.gmtime(cur)))
        cur += 3600
    days_to_scan.add(time.strftime("%Y-%m-%d", time.gmtime(window_end)))
    out: list[dict] = []
    for day in sorted(days_to_scan):
        p = log_root / f"{day}.jsonl"
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("turn-log %s unreadable: %s", p, e)
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = row.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            if window_start <= ts <= window_end:
                out.append(row)
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def load_thread_memories(*, memory_root: Path, bot: str) -> list[dict]:
    """Return one dict per persisted thread memory under
    ``<memory_root>/<bot>/<user_id>/<thread_id>.memory.json``."""
    bot_dir = memory_root / bot
    if not bot_dir.is_dir():
        return []
    out: list[dict] = []
    for user_dir in sorted(bot_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        try:
            user_id = int(user_dir.name)
        except ValueError:
            continue
        for mem_file in sorted(user_dir.glob("*.memory.json")):
            try:
                payload = json.loads(mem_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            thread_id = mem_file.name[: -len(".memory.json")]
            out.append({
                "bot": bot,
                "user_id": user_id,
                "thread_id": thread_id,
                **payload,
            })
    return out
