"""List bots + per-bot status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from gateway.deps import require_device, state


router = APIRouter(prefix="/v1", tags=["bots"])


class BotInfo(BaseModel):
    name: str
    display_name: str
    status: str


@router.get("/bots", response_model=list[BotInfo])
def list_bots(device=Depends(require_device), request: Request = None) -> list[BotInfo]:
    st = state(request)
    return [
        BotInfo(name=a.name, display_name=a.display_name, status=a.status())
        for a in st.adapters.values()
    ]
