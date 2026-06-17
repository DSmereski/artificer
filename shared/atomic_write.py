"""Atomic + durable file writes.

The standard `tmp.write_text(...); tmp.replace(target)` pattern is
*atomic* (the rename is one filesystem op) but **not durable** — on a
hard power-cut, the page-cache copy of `tmp` may not have hit disk yet,
so the rename swaps in a zero-byte or truncated file and the next boot
reads garbage.

Architect's 2026-04-29 review flagged this for the LLM history JSONs:
the user runs the gateway on a gaming PC and the GPU pipeline can OOM-
panic the kernel; if that happens during a state write, the session's
chat history is silently lost on the next start because
`LLMClient._load_all` quietly skips the corrupt file.

`atomic_write_json(path, payload)` here:
  1. write the JSON-encoded bytes to `<path>.tmp` via a file handle
  2. flush + fsync that handle so the bytes are on the platter
  3. tmp.replace(path) — atomic from the kernel's POV
  4. best-effort fsync of the parent directory so the rename's metadata
     is also durable (skipped silently on Windows where there's no
     directory fd to fsync)

Use this for any state file that the gateway re-reads after a crash.
For caches and journals that can be regenerated, the cheaper
non-durable pattern is fine.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any


log = logging.getLogger("shared.atomic_write")


def _scratch_path(target: Path) -> Path:
    """Per-writer unique scratch file alongside `target`.

    A shared `<target>.tmp` would collide if two callers race on the
    same path: on POSIX one would silently truncate the other's bytes,
    and on Windows the second open() would raise PermissionError. PID
    + 64 bits of randomness gives both processes and threads a private
    scratch file each call so the final replace() is the only contended
    op (which is atomic and last-writer-wins, the existing contract)."""
    nonce = f"{os.getpid()}.{secrets.token_hex(8)}"
    return target.with_suffix(target.suffix + f".tmp.{nonce}")


def atomic_write_json(
    path: Path | str,
    payload: Any,
    *,
    indent: int | None = None,
) -> None:
    """Write `payload` as JSON to `path` atomically and durably.

    Raises whatever the underlying write/replace raises. Callers that
    want a swallow-and-warn behaviour should wrap the call themselves.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = _scratch_path(p)
    data = json.dumps(payload, indent=indent).encode("utf-8")

    # Step 1+2: write + fsync the data file. The `with open` / fsync
    # ordering is important — if we ran replace() before fsync the
    # rename could land before the bytes did.
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # Some virtualised filesystems (Docker bind mounts on
                # Windows host, network shares) don't support fsync. Don't
                # fail the write — the user already accepted that storage.
                pass

        # Step 3: atomic rename.
        tmp.replace(p)
    except BaseException:
        # If anything goes wrong before the rename, clean up the scratch
        # file so the directory doesn't accumulate `.tmp.*` debris.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    # Step 4: parent dir fsync. On Linux/macOS the rename's metadata
    # entry needs its own fsync, otherwise the dir entry can be lost
    # while the inode survives. Windows doesn't expose dir fds and
    # raises PermissionError; skip silently.
    try:
        dir_fd = os.open(str(p.parent), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Same durability guarantee as `atomic_write_json` but for raw
    bytes — used by anything writing markdown / safetensors metadata
    that already encodes itself."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = _scratch_path(p)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        tmp.replace(p)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(str(p.parent), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write_text(path: Path | str, text: str) -> None:
    """Same durability as atomic_write_json/bytes, but for text."""
    atomic_write_bytes(path, text.encode("utf-8"))
