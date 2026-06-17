"""Thin ntfy client for self-hosted push notifications.

Spec: https://docs.ntfy.sh/publish/#json-publishing
Payloads carry opaque ids only; the app fetches real content via the
authenticated gateway after waking up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx


log = logging.getLogger("gateway.ntfy")


@dataclass(frozen=True, slots=True)
class NtfyClient:
    base_url: str
    enabled: bool = True
    default_priority: int = 3

    async def publish(
        self,
        *,
        topic: str,
        title: str,
        message: str,
        priority: int | None = None,
        tags: list[str] | None = None,
        click: str | None = None,
    ) -> bool:
        """Publish one notification. Returns False on failure (never raises)."""
        if not self.enabled:
            log.debug("ntfy disabled; skipping publish to %s", topic)
            return False
        body: dict = {
            "topic": topic,
            "title": title,
            "message": message,
            "priority": priority or self.default_priority,
        }
        if tags:
            body["tags"] = tags
        if click:
            body["click"] = click
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
                r = await client.post("/", json=body)
                if r.status_code >= 400:
                    log.warning("ntfy %s returned %d: %s", topic, r.status_code, r.text[:200])
                    return False
                return True
        except httpx.HTTPError as e:
            log.warning("ntfy publish failed: %s", e)
            return False
