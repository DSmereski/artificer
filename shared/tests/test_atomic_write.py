# shared/tests/test_atomic_write.py
"""Atomic-write durability + concurrency tests."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from shared.atomic_write import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)


def test_writes_json_payload(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}


def test_no_tmp_left_behind(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    atomic_write_json(p, {"a": 1})
    leftovers = list(tmp_path.glob("*.tmp*"))
    assert leftovers == []


def test_concurrent_writes_to_same_path_dont_corrupt(tmp_path: Path) -> None:
    """Two threads writing the same target must not produce a torn file.

    Each thread races to write its own payload; the last replace() wins.
    The post-condition we care about: the final file is **one** of the
    two valid payloads — never a half-written, truncated, or mixed
    payload.

    With a shared `<path>.tmp` scratch file the writers race on the same
    inode and a half-flushed write can be renamed in. Per-writer unique
    tmp names eliminate that race.

    Note: Windows can occasionally raise PermissionError when two
    threads call replace() against the same destination at the same
    instant (the kernel briefly holds the dest handle). That's an OS-
    level liveness limitation independent of corruption — callers
    already handle the error path. We tolerate it here and only assert
    the non-corruption invariant on whichever calls succeeded.
    """
    p = tmp_path / "shared.json"
    payload_a = {"who": "a", "blob": "x" * 4096}
    payload_b = {"who": "b", "blob": "y" * 4096}
    succeeded = [0]
    succeeded_lock = threading.Lock()

    def write(payload: dict) -> None:
        for _ in range(20):
            try:
                atomic_write_json(p, payload)
                with succeeded_lock:
                    succeeded[0] += 1
            except PermissionError:
                # Windows replace() race; documented above.
                pass

    t1 = threading.Thread(target=write, args=(payload_a,))
    t2 = threading.Thread(target=write, args=(payload_b,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert succeeded[0] >= 1, "no writes succeeded — concurrency totally broken"
    final = json.loads(p.read_text(encoding="utf-8"))
    assert final in (payload_a, payload_b), (
        "final file does not match either payload — torn write"
    )
    leftovers = list(tmp_path.glob("*.tmp*"))
    assert leftovers == [], f"tmp files left behind: {leftovers}"


def test_text_and_bytes_round_trip(tmp_path: Path) -> None:
    pt = tmp_path / "t.md"
    atomic_write_text(pt, "hello")
    assert pt.read_text(encoding="utf-8") == "hello"

    pb = tmp_path / "b.bin"
    atomic_write_bytes(pb, b"\x00\x01\x02")
    assert pb.read_bytes() == b"\x00\x01\x02"
