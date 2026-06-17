"""In-process event bus + WS fanout.

Publishers can come from the asyncio loop OR from worker threads (e.g.
ImageShim's job thread). Each subscriber records the loop it lives on at
subscribe time; publish uses `call_soon_threadsafe` so any thread can
deliver.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any


log = logging.getLogger("gateway.events")


@dataclass
class _Subscriber:
    queue: asyncio.Queue[dict[str, Any]]
    loop: asyncio.AbstractEventLoop
    name: str
    # Per-subscriber drop counter. Bumped from the subscriber's own
    # event loop in `_put_or_drop`, so reads from any thread are
    # snapshot-only — fine for telemetry.
    drops: int = 0


class EventBus:
    """Thread-safe async fanout with bounded per-subscriber queues."""

    def __init__(self, max_queue_per_subscriber: int = 100) -> None:
        self._subs: list[_Subscriber] = []
        self._lock = threading.Lock()
        self._depth = max_queue_per_subscriber

    async def subscribe(self, name: str) -> asyncio.Queue[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._depth)
        with self._lock:
            self._subs.append(_Subscriber(queue=q, loop=loop, name=name))
        return q

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [s for s in self._subs if s.queue is not queue]

    def publish(self, event: dict[str, Any]) -> None:
        """Non-blocking, thread-safe. Drops events on full queues."""
        with self._lock:
            subs = list(self._subs)
        for s in subs:
            try:
                s.loop.call_soon_threadsafe(
                    _put_or_drop, s, event,
                )
            except RuntimeError:
                # Loop closed — subscriber vanished; ignore.
                log.debug("subscriber %s loop closed; dropping", s.name)

    def drop_stats(self) -> dict[str, int]:
        """Return per-subscriber drop counts. Telemetry-only — not a
        guarantee of consistency since we read without the loop. The
        architect's review flagged silent drops as the likely cause of
        'where did my image_done go?' on a slow Tailscale link; this
        gives the next debugger a number instead of a guess."""
        with self._lock:
            return {s.name: s.drops for s in self._subs}


def _put_or_drop(subscriber: _Subscriber, event: dict) -> None:
    try:
        subscriber.queue.put_nowait(event)
    except asyncio.QueueFull:
        # The first drop is loud; subsequent drops to the same
        # subscriber are quiet (every 50th) so a slow client can't
        # flood the gateway log.
        subscriber.drops += 1
        if subscriber.drops == 1 or subscriber.drops % 50 == 0:
            log.warning(
                "subscriber %s queue full; dropped event %s "
                "(total drops: %d)",
                subscriber.name, event.get("type"), subscriber.drops,
            )
