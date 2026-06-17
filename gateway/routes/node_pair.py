"""Hive node pairing endpoint.

Mirrors `gateway/routes/pair.py` but for compute nodes:
- Code (6 digits, owner-issued) + node-supplied capability snapshot
  are POSTed.
- Gateway claims the code, mints a per-node Bearer, persists the node
  + initial capability snapshot, returns {node_id, token, name}.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.auth import issue_token
from gateway.deps import state


log = logging.getLogger("gateway.routes.node_pair")

router = APIRouter(prefix="/v1/pair", tags=["hive-pair"])


class PairNodeRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=16)
    name: str = Field("", max_length=64)
    capabilities: dict = Field(default_factory=dict)


class PairNodeResponse(BaseModel):
    node_id: str
    token: str
    name: str


def _client_ip(request: Request) -> str:
    """Anonymous pair endpoint sees raw socket IP. Behind a reverse
    proxy this needs X-Forwarded-For; Phase 1 runs on tailnet so the
    socket IP is the real source.
    """
    return request.client.host if request.client else "unknown"


@router.post("/node", response_model=PairNodeResponse)
def pair_node(body: PairNodeRequest, request: Request) -> PairNodeResponse:
    st = state(request)
    ip = _client_ip(request)
    limiter = getattr(st, "rate_limiter", None)
    if limiter is not None and not limiter.try_acquire(ip, "pair_attempts"):
        log.warning("pair/node rate limited: ip=%s", ip)
        raise HTTPException(
            status_code=429, detail="too many pair attempts; slow down",
        )
    if not st.node_invites.claim(body.code.strip()):
        log.info("pair/node invalid code: ip=%s", ip)
        raise HTTPException(status_code=401, detail="invalid or expired invite code")

    token = issue_token(st.config.nodes.token_bytes)
    name = body.name.strip() or "unnamed-node"

    labels_raw = body.capabilities.get("labels") or []
    labels = tuple(
        str(lbl) for lbl in labels_raw if isinstance(lbl, (str, int))
    )

    node = st.node_registry.add(
        name=name,
        token=token,
        labels=labels,
    )
    # Record the initial capability snapshot as the first heartbeat.
    if body.capabilities:
        st.node_registry.record_heartbeat(node.id, body.capabilities)
    log.info("node paired: id=%s name=%s ip=%s", node.id, node.name, ip)
    return PairNodeResponse(node_id=node.id, token=token, name=node.name)
