"""Hive node registry endpoints.

- GET    /v1/nodes              owner lists paired nodes
- GET    /v1/nodes/{id}         owner reads one node + latest caps
- DELETE /v1/nodes/{id}         owner removes a node (token-revoke)
- POST   /v1/nodes/{id}/heartbeat   node self-heartbeat (every 15s)
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.deps import require_device, require_node, state
from gateway.worker_pool.registry import HiveNode


log = logging.getLogger("gateway.routes.nodes")

# Reject heartbeats whose serialized capability snapshot exceeds this
# byte count. Phase 1 snapshots are ~1KB; 32KB leaves headroom for many
# GPUs / runtimes without giving a malicious agent room to DoS the host.
_HEARTBEAT_MAX_BYTES = 32 * 1024


router = APIRouter(prefix="/v1/nodes", tags=["hive-nodes"])


class NodeSummary(BaseModel):
    id: str
    name: str
    status: str  # "online" | "offline"
    agent_version: str
    last_seen: float
    labels: list[str]


class NodeDetail(NodeSummary):
    created: float
    capabilities: dict


class HeartbeatResponse(BaseModel):
    ok: bool
    server_time: float
    # Phase 2 will populate this with `{id, kind, payload, deadline}`
    # entries the agent should pick up. Phase 1 always returns []; ships
    # the field now so the agent's response handling is forward-compatible.
    jobs: list[dict] = Field(default_factory=list)


def _status(last_seen: float, offline_after_s: int) -> str:
    return "online" if (time.time() - last_seen) < offline_after_s else "offline"


def _summary(node: HiveNode, offline_after_s: int) -> NodeSummary:
    return NodeSummary(
        id=node.id,
        name=node.name,
        status=_status(node.last_seen, offline_after_s),
        agent_version=node.agent_version,
        last_seen=node.last_seen,
        labels=list(node.labels),
    )


@router.get("", response_model=list[NodeSummary])
def list_nodes(
    request: Request, device=Depends(require_device),
) -> list[NodeSummary]:
    st = state(request)
    offline = st.config.nodes.heartbeat_offline_seconds
    return [_summary(n, offline) for n in st.node_registry.list_active()]


@router.get("/{node_id}", response_model=NodeDetail)
def get_node(
    node_id: str, request: Request, device=Depends(require_device),
) -> NodeDetail:
    st = state(request)
    node = st.node_registry.get(node_id)
    if node is None or node.revoked:
        raise HTTPException(status_code=404, detail="node not found")
    offline = st.config.nodes.heartbeat_offline_seconds
    base = _summary(node, offline)
    try:
        caps = json.loads(node.capabilities_json)
    except json.JSONDecodeError:
        caps = {}
    return NodeDetail(
        **base.model_dump(),
        created=node.created,
        capabilities=caps,
    )


@router.delete("/{node_id}", status_code=204)
def delete_node(
    node_id: str, request: Request, device=Depends(require_device),
) -> None:
    st = state(request)
    if not st.node_registry.purge(node_id):
        raise HTTPException(status_code=404, detail="node not found")
    log.info("node removed: id=%s", node_id)


class HeartbeatBody(BaseModel):
    agent_version: str = Field("", max_length=32)
    # Capability snapshot — schema left open here; the registry stores
    # the JSON verbatim. The agent owns the schema in `probe.py`.
    model_config = {"extra": "allow"}


@router.post("/{node_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    node_id: str,
    body: HeartbeatBody,
    request: Request,
    node=Depends(require_node),
) -> HeartbeatResponse:
    if node.id != node_id:
        # Node A's token cannot heartbeat for node B's id.
        raise HTTPException(status_code=403, detail="node id mismatch")
    st = state(request)
    payload = body.model_dump()
    # Cap stored snapshot size — extra="allow" on HeartbeatBody otherwise
    # lets a compromised agent ship arbitrary-size JSON that the registry
    # would write under the global write lock on every 15s heartbeat.
    serialised_size = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
    if serialised_size > _HEARTBEAT_MAX_BYTES:
        log.warning(
            "heartbeat rejected: oversized payload node=%s size=%d limit=%d",
            node_id, serialised_size, _HEARTBEAT_MAX_BYTES,
        )
        raise HTTPException(
            status_code=413,
            detail=f"capability snapshot too large ({serialised_size} > {_HEARTBEAT_MAX_BYTES})",
        )
    try:
        st.node_registry.record_heartbeat(node_id, payload)
    except ValueError:
        # Node was purged between auth and write (race vs DELETE), or
        # the registry has lost the row. Surface as 404 so the agent
        # gets an actionable signal rather than an opaque 500.
        log.warning("heartbeat for unknown node id=%s", node_id)
        raise HTTPException(status_code=404, detail="node not found")
    return HeartbeatResponse(ok=True, server_time=time.time())
