"""WS /v1/events — server-pushed notifications (scout alerts, image-done)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gateway.deps import authenticate_ws, state


router = APIRouter(prefix="/v1", tags=["events"])
log = logging.getLogger("gateway.events_ws")


@router.websocket("/events")
async def events_ws(websocket: WebSocket) -> None:
    app_state = state(websocket)
    device = await authenticate_ws(websocket, app_state)
    if device is None:
        return

    bus = app_state.event_bus
    if bus is None:
        await websocket.close(reason="no event bus")
        return

    await websocket.accept()
    queue = await bus.subscribe(f"ws:{device.id}")
    try:
        while True:
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except Exception:  # noqa: BLE001
                break
    except WebSocketDisconnect:
        pass
    finally:
        await bus.unsubscribe(queue)
