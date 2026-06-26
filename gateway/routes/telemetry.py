"""GET /v1/telemetry — recent hive turn metrics + per-turn debug log
for the app dev panel and CLI troubleshooting.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from gateway.deps import require_device, state


router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])


@router.get("/last_turn")
def last_turn(
    n: int = Query(default=20, ge=1, le=100),
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    st = state(request)
    tel = st.turn_telemetry
    if tel is None:
        raise HTTPException(503, "telemetry not initialised")
    return {"records": tel.to_jsonable(n=n)}


@router.get("/turn_log")
def turn_log(
    n: int = Query(default=20, ge=1, le=200),
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Tail of the structured per-turn debug log.

    Each entry includes: planner summary + raw preview + error;
    per-helper role/model/latency/tokens/raw preview/error; synthesis
    reply/raw/error; action receipts; final reply.

    Use to debug "why did Hive say X" without grovelling through
    journal logs.
    """
    st = state(request)
    store = st.turn_log_store
    if store is None:
        raise HTTPException(503, "turn-log store not initialised")
    return {"entries": store.tail(n=n)}


@router.get("/turn_log/files")
def turn_log_files(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """List the on-disk JSONL log files (one per UTC date)."""
    st = state(request)
    store = st.turn_log_store
    if store is None:
        raise HTTPException(503, "turn-log store not initialised")
    return {
        "files": [
            {"name": p.name, "size": p.stat().st_size, "path": str(p)}
            for p in store.files()
        ],
    }
