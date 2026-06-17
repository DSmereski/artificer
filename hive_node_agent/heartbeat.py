"""Heartbeat loop: every N seconds POST capability snapshot to host."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hive_node_agent.client import post_json
from hive_node_agent.config import NodeAgentConfig
from hive_node_agent.probe import collect


log = logging.getLogger("hive_node_agent.heartbeat")


def _heartbeat_url(cfg: NodeAgentConfig) -> str:
    return cfg.host_url.rstrip("/") + f"/v1/nodes/{cfg.node_id}/heartbeat"


async def send_one(
    cfg: NodeAgentConfig, *, capabilities: dict[str, Any],
) -> dict[str, Any]:
    if not cfg.paired:
        raise RuntimeError("agent not paired — cannot send heartbeat")
    return await post_json(
        _heartbeat_url(cfg),
        capabilities,
        token=cfg.token,
    )


async def run_heartbeat_loop(cfg: NodeAgentConfig) -> None:
    """Fire heartbeats until cancelled. Backoff on failure (capped 30s)."""
    if not cfg.paired:
        raise RuntimeError("agent not paired — refusing to start loop")
    backoff = 0.0
    while True:
        try:
            snapshot = collect(labels=cfg.labels)
            await send_one(cfg, capabilities=snapshot)
            backoff = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("heartbeat failed: %s", e)
            backoff = min(30.0, max(2.0, backoff * 2 if backoff else 2.0))
        delay = backoff if backoff else cfg.heartbeat_interval_s
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
