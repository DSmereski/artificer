"""GET/POST /v1/hive — debug + introspection of the M2 hive.

Endpoints:
  GET  /v1/hive/info      → catalog + helper roster + budget
  POST /v1/hive/test      → run a coordinator turn end-to-end with the
                            given user_msg; returns the full event tree
                            and the assistant reply. Useful for smoke
                            testing without going through chat WS.

These are auth-gated like every other v1 endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gateway.deps import require_device, state
from gateway.event_emitter import ListEmitter
from gateway.hive_coordinator import TurnContext


router = APIRouter(prefix="/v1/hive", tags=["hive"])
log = logging.getLogger("gateway.hive_route")


@router.get("/info")
def hive_info(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    coord = st.hive_coordinator
    catalog = st.model_catalog
    if coord is None or catalog is None:
        raise HTTPException(503, "hive not initialised")
    return {
        "helper_roles": list(st.helpers.keys()),
        "models_loaded": catalog.model_ids,
        "budget": asdict(coord.budget),
    }


class HiveTestRequest(BaseModel):
    user_msg: str
    device_id: str = "hive-test"
    user_id: int = 0


@router.post("/test")
async def hive_test(
    body: HiveTestRequest,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Run a single coordinator turn and return the full event trace.

    Helpers really call Ollama, so the response includes real model
    outputs. Use sparingly — each turn can take 30-90 s.
    """
    st = state(request)
    coord = st.hive_coordinator
    if coord is None:
        raise HTTPException(503, "hive not initialised")

    emitter = ListEmitter()
    # M5.1: surface the device's in-progress image build so the
    # planner sees it in inputs.
    build_store = st.image_build_store
    image_build = None
    if build_store is not None:
        bs = build_store.get(body.device_id)
        if bs is not None:
            from dataclasses import asdict
            image_build = asdict(bs)
    # M3: skills digest
    skills_digest = ""
    reg = st.skill_registry
    if reg is not None:
        reg.reload_if_changed()
        skills_digest = reg.digest_for_planner(audience="hive")

    ctx = TurnContext(
        user_msg=body.user_msg,
        user_id=body.user_id, device_id=body.device_id,
        bot="hive",
        history_digest="",
        image_build=image_build,
        skills_digest=skills_digest,
        available_helpers=list(st.helpers.keys()),
    )
    turn = await coord.coordinate(ctx, emitter)

    # M6.3: record the turn for telemetry.
    tel = st.turn_telemetry
    if tel is not None:
        from gateway.turn_telemetry import TurnRecord
        import time
        tel.record(TurnRecord(
            ts=time.time(),
            turn_id=(emitter.events[0].id if emitter.events else "?"),
            bot="hive",
            user_msg_preview=body.user_msg[:240],
            helpers_used=list(turn.helpers_used),
            total_tokens=turn.total_tokens,
            total_latency_ms=turn.total_latency_ms,
            blocked=turn.blocked,
            error=turn.error,
            actions=[
                str(a.get("verb", "?"))
                for a in turn.actions if isinstance(a, dict)
            ],
        ))

    # M5.1: persist any build_updates from synthesis.
    if build_store is not None and turn.actions:
        for a in turn.actions:
            if isinstance(a, dict) and a.get("verb") == "image_build_update":
                payload = a.get("payload") or {}
                build_store.update(body.device_id, payload)

    return {
        "reply": turn.reply,
        "actions": turn.actions,
        "blocked": turn.blocked,
        "error": turn.error,
        "helpers_used": turn.helpers_used,
        "total_tokens": turn.total_tokens,
        "total_latency_ms": turn.total_latency_ms,
        "events": [
            {
                "type": e.type, "id": e.id, "parent": e.parent,
                "ts": e.ts, **e.payload,
            }
            for e in emitter.events
        ],
    }
