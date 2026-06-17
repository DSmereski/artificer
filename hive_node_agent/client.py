"""Tiny httpx wrapper used by pairing + heartbeat.

Kept small so tests can monkeypatch a single function. Async; the
heartbeat loop runs on the agent's asyncio loop and pairing runs in
the wizard handler.
"""

from __future__ import annotations

from typing import Any

import httpx


DEFAULT_TIMEOUT_S = 10.0


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


async def get_json(
    url: str,
    *,
    token: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any] | None:
    """GET with optional Bearer. Returns parsed JSON, or None on 204."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    if not resp.content:
        return None
    return resp.json()
