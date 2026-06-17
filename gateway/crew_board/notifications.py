"""Crew Board notifications — fan-out to subscribers + ntfy push.

Subscribers are WebSockets connected to `/board/events`. Each
notification is a small JSON dict (`event`, `task`, optional fields).
ntfy push is fire-and-forget — failure shouldn't block the dispatcher.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

log = logging.getLogger("gateway.crew_board.notify")


class CrewNotifier:
    def __init__(
        self, *,
        ntfy_topic: str | None = None,
        ntfy_base: str = "https://ntfy.sh",
        event_bus: Any = None,
    ) -> None:
        self._subscribers: set[Any] = set()
        self._ntfy_topic = ntfy_topic
        self._ntfy_base = ntfy_base.rstrip("/")
        self._lock = asyncio.Lock()
        # Optional main EventBus — board events are also published here so
        # the app's /v1/events listener can fire an in-app notification +
        # deep-link to the Crew Board (alongside /board/events + ntfy).
        self._event_bus = event_bus

    async def subscribe(self, ws) -> None:
        async with self._lock:
            self._subscribers.add(ws)

    async def unsubscribe(self, ws) -> None:
        async with self._lock:
            self._subscribers.discard(ws)

    def broadcast(self, payload: dict) -> None:
        """Sync broadcast — spawned as a background task per recipient."""
        message = {"type": "board_event", **payload}
        for ws in list(self._subscribers):
            asyncio.create_task(self._send_one(ws, message))
        if self._ntfy_topic:
            asyncio.create_task(self._ntfy_push(message))
        # Mirror onto the main event bus (/v1/events) so the app's
        # foreground listener can notify + deep-link. Best-effort.
        if self._event_bus is not None:
            try:
                self._event_bus.publish(message)
            except Exception:  # noqa: BLE001
                log.debug("event_bus publish failed", exc_info=True)

    async def _send_one(self, ws, message: dict) -> None:
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001
            log.debug("subscriber send failed; dropping", exc_info=True)
            await self.unsubscribe(ws)

    async def _ntfy_push(self, message: dict) -> None:
        if not self._ntfy_topic:
            return
        title = f"Crew Board: {message.get('event', 'event')}"
        body = message.get("task") or json.dumps(message)
        url = f"{self._ntfy_base}/{self._ntfy_topic}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(url, headers={"Title": title}, content=body)
        except httpx.RequestError as e:
            log.debug("ntfy push failed: %s", e)
