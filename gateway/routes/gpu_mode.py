"""/v1/gpu-mode — the "free the 4080" switch.

GET  -> {mode, gaming, ai_may_use_4080, ai_devices}
PUT  {mode: auto|force_on|force_off} -> same status after the change.

mode auto      : AI borrows the 4080 only when not gaming (default)
mode force_on  : AI always allowed on the 4080
mode force_off : the off switch — reserve the 4080 for gaming always
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from gateway import gpu_policy

router = APIRouter(prefix="/v1", tags=["gpu"])


@router.get("/gpu-mode")
async def get_gpu_mode() -> JSONResponse:
    return JSONResponse(await gpu_policy.status())


@router.put("/gpu-mode")
async def put_gpu_mode(mode: str = Body(..., embed=True)) -> JSONResponse:
    try:
        gpu_policy.set_mode(mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(await gpu_policy.status())
