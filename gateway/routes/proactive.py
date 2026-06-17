"""Proactive Hive endpoint — lets scout_daemon trigger a Hive turn.

POST /v1/proactive/trigger
  {
    "reason": "GPU 1 temperature critical: 94 C",
    "context": "optional extra detail for the planner",
    "audience": "owner"        # optional, default "owner"
  }

The coordinator runs a synthetic TurnContext, then routes the reply to
ntfy and (optionally) the event bus.  A single turn-in-progress guard
prevents a proactive trigger from spawning a second proactive turn,
avoiding feedback loops.

Authentication is internal-only: the endpoint is bound to localhost and
requires the gateway auth token, same as all other /v1 routes.  When
running in tests, calls use a fake coordinator so no real LLM is hit.

Config gate: this endpoint always exists once wired, but the scout
daemon only calls it when `SCOUT_PROACTIVE_HIVE_ENABLED=true` is set
in the environment / config/.env — the flag lives in the scout config,
not here.  Here we just run the turn when asked.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.deps import require_device, state


router = APIRouter(prefix="/v1/proactive", tags=["proactive"])
log = logging.getLogger("gateway.proactive")

# Guard: one proactive turn at a time. A second POST while a proactive
# turn is running returns 429 immediately — prevents feedback loops.
_proactive_in_flight = False
_PROACTIVE_TURN_TIMEOUT_S = 60.0


class ProactiveTriggerRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)
    context: str = Field(default="", max_length=2000)
    audience: str = Field(default="owner", max_length=50)


class ProactiveTriggerResponse(BaseModel):
    ok: bool
    reply_preview: str = ""
    detail: str = ""


@router.post("/trigger", response_model=ProactiveTriggerResponse)
async def proactive_trigger(
    body: ProactiveTriggerRequest,
    device=Depends(require_device),
    request: Request = None,
) -> ProactiveTriggerResponse:
    """Run a synthetic Hive turn from a scout-detected event.

    The reply is pushed to ntfy (topic ai-team-proactive) so the owner
    is notified on their phone.  The event bus also receives a
    `hive_proactive_done` event so UI subscribers can display it.

    Returns immediately with 429 if a proactive turn is already running
    to prevent feedback loops.
    """
    global _proactive_in_flight
    if _proactive_in_flight:
        raise HTTPException(
            status_code=429,
            detail="proactive turn already in flight; skipping to prevent feedback loop",
        )

    st = state(request)
    coordinator = st.hive_coordinator
    if coordinator is None:
        raise HTTPException(
            status_code=503,
            detail="hive coordinator not available",
        )

    _proactive_in_flight = True
    try:
        reply, detail = await asyncio.wait_for(
            _run_proactive_turn(st, coordinator, body),
            timeout=_PROACTIVE_TURN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        _proactive_in_flight = False
        log.warning("proactive turn timed out after %.0fs", _PROACTIVE_TURN_TIMEOUT_S)
        return ProactiveTriggerResponse(
            ok=False, detail=f"turn timed out after {_PROACTIVE_TURN_TIMEOUT_S:.0f}s",
        )
    except Exception as e:  # noqa: BLE001
        _proactive_in_flight = False
        log.exception("proactive turn raised unexpectedly")
        return ProactiveTriggerResponse(
            ok=False, detail=f"{type(e).__name__}: {e}",
        )
    else:
        _proactive_in_flight = False
        return ProactiveTriggerResponse(ok=True, reply_preview=reply, detail=detail)


async def _run_proactive_turn(st, coordinator, body: ProactiveTriggerRequest) -> tuple[str, str]:
    """Build a synthetic TurnContext, run coordinate(), push ntfy + event bus.

    Returns (reply_preview, detail). Never raises — callers depend on
    the no-raise contract.
    """
    from gateway.event_emitter import ListEmitter
    from gateway.hive_coordinator import TurnContext

    # Synthetic user message: "Hive, <reason>. <context>"
    user_msg = f"Hive notice: {body.reason}"
    if body.context.strip():
        user_msg += f"\n\nContext: {body.context.strip()}"

    # Use a stable synthetic user_id so turn-log queries can filter
    # proactive turns. Chosen to be outside the real user-id space.
    PROACTIVE_USER_ID = 0

    ctx = TurnContext(
        user_msg=user_msg,
        user_id=PROACTIVE_USER_ID,
        device_id="proactive",
        bot="terry",
        available_helpers=[],
        device_audience=[body.audience],
    )

    emitter = ListEmitter()
    try:
        turn = await coordinator.coordinate(ctx, emitter)
    except Exception as e:  # noqa: BLE001
        log.exception("proactive coordinator.coordinate raised")
        return "", f"{type(e).__name__}: {e}"

    reply = (turn.reply or "").strip()
    if not reply or turn.blocked or turn.error:
        detail = turn.error or "blocked or empty"
        log.warning("proactive turn produced no reply: %s", detail)
        return "", detail

    log.info("proactive turn done, preview=%r", reply[:80])

    # Push to ntfy — best effort.
    ntfy = st.ntfy
    if ntfy is not None and getattr(ntfy, "enabled", False):
        try:
            await ntfy.publish(
                topic="ai-team-proactive",
                title=f"Hive noticed: {body.reason[:60]}",
                message=reply[:200],
                tags=["robot"],
                priority=3,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("proactive ntfy publish failed: %s", e)

    # Publish to event bus — best effort.
    bus = st.event_bus
    if bus is not None:
        try:
            bus.publish({
                "type": "hive_proactive_done",
                "reason": body.reason,
                "preview": reply[:200],
            })
        except Exception as e:  # noqa: BLE001
            log.warning("proactive event_bus publish failed: %s", e)

    return reply[:200], "ok"
