"""Pair with the hive host — claim an invite code, persist Bearer token."""

from __future__ import annotations

from typing import Any

from hive_node_agent.client import post_json
from hive_node_agent.config import NodeAgentConfig


def _join(host_url: str, path: str) -> str:
    return host_url.rstrip("/") + path


async def pair_with_host(
    cfg: NodeAgentConfig,
    *,
    host_url: str,
    code: str,
    name: str,
    capabilities: dict[str, Any],
) -> NodeAgentConfig:
    """POST /v1/pair/node, store token + node_id in config, return new config."""
    url = _join(host_url, "/v1/pair/node")
    resp = await post_json(url, {
        "code": code,
        "name": name,
        "capabilities": capabilities,
    })
    token = str(resp["token"])
    node_id = str(resp["node_id"])
    persisted_name = str(resp.get("name") or name)
    caps_labels = capabilities.get("labels")
    labels_tuple: tuple[str, ...] | None = None
    if isinstance(caps_labels, (list, tuple)):
        labels_tuple = tuple(str(x) for x in caps_labels)
    new_cfg = cfg.with_pairing(
        host_url=host_url.rstrip("/"),
        token=token,
        node_id=node_id,
        name=persisted_name,
        labels=labels_tuple,
    )
    new_cfg.save()
    return new_cfg
