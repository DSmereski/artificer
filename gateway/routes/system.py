"""System-level meta endpoints.

GET /v1/system/concurrency — surfaces the live helper-fan-out cap,
including whether it's currently throttled because the user is gaming on
GPU 0. Used by the Settings screen to show the user what the gateway
will do, and by the bench script to assert the running cap before
sweeping.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from gateway.deps import require_device, state
from gateway.hive_coordinator import _gaming_on_gpu0


router = APIRouter(prefix="/v1/system", tags=["system"])
log = logging.getLogger("gateway.system")


class ConcurrencyInfo(BaseModel):
    """Snapshot of how many helpers the hive will dispatch in parallel."""
    full_cap: int
    gaming_cap: int
    current_cap: int
    gaming_detected: bool


@router.get("/concurrency", response_model=ConcurrencyInfo)
def get_concurrency(
    request: Request,
    device=Depends(require_device),
) -> ConcurrencyInfo:
    st = state(request)
    # The coordinator's TurnBudget owns the caps. Reading it directly
    # keeps a single source of truth — no env-var reparsing here.
    coord = st.hive_coordinator
    full_cap = 5
    gaming_cap = 3
    if coord is not None and getattr(coord, "budget", None) is not None:
        full_cap = coord.budget.max_concurrent_helpers
        gaming_cap = coord.budget.gaming_concurrent_helpers
    gaming = _gaming_on_gpu0()
    current = gaming_cap if gaming else full_cap
    return ConcurrencyInfo(
        full_cap=full_cap,
        gaming_cap=gaming_cap,
        current_cap=current,
        gaming_detected=gaming,
    )
