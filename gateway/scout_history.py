"""Rolling on-disk history of Scout status samples.

Append-only JSONL of per-sample snapshots. Bounded by total bytes so we
don't silently grow forever. The gateway samples on a timer and trims
the oldest lines when the file exceeds the cap.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class ScoutHistory:
    def __init__(self, path: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._lock = threading.Lock()

    def append(self, snapshot: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {"ts": time.time(), **snapshot}
        line = json.dumps(snapshot, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)
            try:
                size = self._path.stat().st_size
            except OSError:
                return
            if size > self._max_bytes:
                self._trim_locked()

    def read(self, *, since: float | None = None, limit: int | None = None) -> list[dict]:
        if not self._path.exists():
            return []
        out: list[dict] = []
        with self._lock, self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None and obj.get("ts", 0) < since:
                    continue
                out.append(obj)
        if limit is not None and len(out) > limit:
            out = out[-limit:]
        return out

    def _trim_locked(self) -> None:
        """Drop the oldest ~half of the file."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        lines = raw.splitlines()
        keep = lines[len(lines) // 2 :]
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        tmp.replace(self._path)
