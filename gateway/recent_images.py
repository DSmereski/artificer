"""Per-device image-job ledger.

Why this exists: when the user closes the app or backgrounds the phone,
the chat WebSocket drops. The image job keeps running on the gateway, but
the original `image_done` event has no live subscriber when it fires. The
finished image becomes orphaned — visible on disk via the media route,
but invisible in chat.

Solution: a small ledger that
  1. records every image job a device started, with its prompt + bot,
  2. subscribes to the EventBus once at startup and updates the matching
     entry when `image_done` arrives — regardless of whether anyone else
     is listening, and
  3. exposes `recent(device_id, since_ts)` so the app can pull a punch list
     of completed jobs on reconnect and render them in chat.

The ledger is persisted to disk (newline-JSON in `state_dir/recent-images.jsonl`)
so a gateway restart doesn't strand finished images mid-flight. Without
this the user gets the "I was never sent an image" failure mode reported
on 2026-04-28: image renders to disk, gateway bounces, recent_images
returns empty on app reconnect, image bubble never appears in chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


log = logging.getLogger("gateway.recent_images")


@dataclass
class RecentJob:
    job_id: str
    device_id: str
    bot: str
    prompt: str
    created_at: float
    state: str = "running"           # running | done | error
    result_ids: list[str] = field(default_factory=list)
    error: str | None = None


class RecentImagesStore:
    """Thread-safe per-device job ledger with bounded retention.

    Persistence: when constructed with a `path`, every mutation
    rewrites the file atomically (tmp + os.replace). Load happens
    once on `load()` — caller wires that into gateway startup.
    """

    def __init__(
        self,
        *,
        max_per_device: int = 50,
        retention_seconds: float = 24 * 3600,
        path: Path | None = None,
    ) -> None:
        self._jobs: dict[str, RecentJob] = {}                  # job_id → job
        self._by_device: dict[str, list[str]] = {}             # device_id → [job_id]
        self._lock = threading.Lock()
        self._max = max_per_device
        self._retention = retention_seconds
        self._path = path

    def load(self) -> int:
        """Read persisted jobs from disk. Returns the count loaded.
        Silently skips malformed lines so a half-written file doesn't
        bring the gateway down."""
        if self._path is None or not self._path.exists():
            return 0
        loaded = 0
        now = time.time()
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        job = RecentJob(**d)
                    except TypeError:
                        continue
                    if now - job.created_at > self._retention:
                        continue
                    self._jobs[job.job_id] = job
                    self._by_device.setdefault(job.device_id, []).append(job.job_id)
                    loaded += 1
        except OSError as e:
            log.warning("recent_images: couldn't read %s: %s", self._path, e)
        return loaded

    def _persist_locked(self) -> None:
        """Caller already holds `self._lock`. Atomic + durable rewrite.

        flush + fsync before the rename so a power-cut on the gaming
        PC doesn't truncate the JSONL ledger and lose recent renders
        from the gallery — without these, the page-cache copy can be
        lost while the rename's directory entry survives.
        """
        if self._path is None:
            return
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as fh:
                for job in self._jobs.values():
                    fh.write(json.dumps(asdict(job), default=str) + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # Network FS / virtualised mounts may reject fsync.
                    pass
            os.replace(tmp, self._path)
        except OSError as e:
            log.warning("recent_images: couldn't persist %s: %s", self._path, e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    def record(
        self,
        *,
        device_id: str,
        bot: str,
        job_id: str,
        prompt: str,
    ) -> None:
        now = time.time()
        with self._lock:
            self._jobs[job_id] = RecentJob(
                job_id=job_id, device_id=device_id, bot=bot,
                prompt=prompt, created_at=now,
            )
            self._by_device.setdefault(device_id, []).append(job_id)
            self._evict_locked(device_id, now)
            self._persist_locked()

    def update_completion(
        self,
        *,
        job_id: str,
        state: str,
        result_ids: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Called from EventBus subscriber when an image_done fires."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                # Job not tracked (e.g. created before the store existed,
                # or via a non-Hive path). Ignore; can't tie it to a device.
                return
            job.state = state
            if result_ids is not None:
                job.result_ids = list(result_ids)
            if error is not None:
                job.error = error
            self._persist_locked()

    def recent(
        self,
        *,
        device_id: str,
        since_ts: float = 0.0,
        bot: str | None = None,
    ) -> list[RecentJob]:
        """Return this device's jobs created at/after `since_ts`, newest first.

        Caller filters on `state` if they only want completed ones.
        """
        with self._lock:
            ids = list(self._by_device.get(device_id, ()))
            now = time.time()
            self._evict_locked(device_id, now)
            jobs: list[RecentJob] = []
            for jid in ids:
                j = self._jobs.get(jid)
                if j is None:
                    continue
                if j.created_at < since_ts:
                    continue
                if bot is not None and j.bot != bot:
                    continue
                jobs.append(j)
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def all_recent(
        self,
        *,
        since_ts: float = 0.0,
        bot: str | None = None,
        limit: int | None = None,
    ) -> list[RecentJob]:
        """Cross-device recent jobs view. Used by the Gallery tab so a
        render fired from phone is visible from PC and vice versa.
        Newest first; optional `limit` truncates."""
        with self._lock:
            jobs = [
                j for j in self._jobs.values()
                if j.created_at >= since_ts
                and (bot is None or j.bot == bot)
            ]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        if limit is not None:
            jobs = jobs[: max(1, int(limit))]
        return jobs

    def _evict_locked(self, device_id: str, now: float) -> None:
        """Drop jobs over the per-device cap or past retention. Caller holds lock."""
        ids = self._by_device.get(device_id) or []
        # Drop expired first.
        live: list[str] = []
        for jid in ids:
            j = self._jobs.get(jid)
            if j is None:
                continue
            if now - j.created_at > self._retention:
                self._jobs.pop(jid, None)
                continue
            live.append(jid)
        # Then trim oldest beyond the cap.
        if len(live) > self._max:
            drop, live = live[: -self._max], live[-self._max :]
            for jid in drop:
                self._jobs.pop(jid, None)
        self._by_device[device_id] = live

    # --------------------------------------------------------------- bus integration

    def attach_to_bus(self, event_bus: Any) -> asyncio.Task:
        """Subscribe to image_done events forever. Returns the consumer task."""
        async def _consume() -> None:
            queue = await event_bus.subscribe("recent-images-store")
            try:
                while True:
                    event = await queue.get()
                    if event.get("type") != "image_done":
                        continue
                    self.update_completion(
                        job_id=str(event.get("job_id", "")),
                        state=str(event.get("state", "done")),
                        result_ids=list(event.get("result_ids", []) or []),
                        error=event.get("error"),
                    )
            except asyncio.CancelledError:
                raise
            finally:
                try:
                    await event_bus.unsubscribe(queue)
                except Exception:  # noqa: BLE001
                    log.debug("unsubscribe failed during shutdown", exc_info=True)

        return asyncio.create_task(_consume(), name="recent-images-consumer")


def job_to_json(j: RecentJob) -> dict:
    """Stable wire representation for the /v1/images/recent endpoint."""
    return asdict(j)
