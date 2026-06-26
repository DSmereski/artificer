"""Bridge that forwards hive-path `image_render` results to the chat WS.

Hive turns queue an image render via `image_shim.enqueue` and return
immediately — the actual generation runs in `imageToVideo`'s GPU
worker thread. Without this bridge the chat WebSocket would see the
assistant's text reply and then nothing, so the app would show "Hive
is rendering…" forever.

Two pieces:

  1. `forward_image_receipts` — call once after `coord.coordinate()`
     returns. Iterates the turn's receipts, sends `image_pending` for
     each queued render, records into `recent_images`, and spawns a
     watcher task per job.
  2. `watch_image_done_and_forward` — the per-job watcher. Subscribes
     to the EventBus and forwards the matching `image_done` /
     `image_error` event back through the WS.

Lives in its own module because the architect's 2026-04-29 review
flagged `routes/chat.py` (1295 LoC at peak) as a single-file overload
mixing WS, web search, vault writes, and the image bridge. Pulling
the bridge out is the smallest extraction that materially shrinks the
route and gives the bridge a place to grow tests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

from gateway.deps import track_background_task


log = logging.getLogger("gateway.chat_image_bridge")


async def watch_image_done_and_forward(
    websocket: WebSocket, bus, job_id: str, *, timeout: float = 600.0,
) -> None:
    """Wait for the matching `image_done` and forward it to the WS.

    Terminates on:
      - the matching event arriving
      - `timeout` seconds (image_app stalled / crashed)
      - WS send failure (client disconnected)
    """
    queue = await bus.subscribe(f"hive-image-{job_id}")
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                try:
                    await websocket.send_json({
                        "type": "image_slow", "job_id": job_id,
                        "message": "still rendering; will appear when ready",
                    })
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            if event.get("type") != "image_done" or event.get("job_id") != job_id:
                continue
            try:
                if event.get("state") == "done" and event.get("result_ids"):
                    await websocket.send_json({
                        "type": "image_done", "job_id": job_id,
                        "media_id": event["result_ids"][0],
                    })
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": event.get("error") or "image failed",
                    })
            except Exception as e:  # noqa: BLE001
                log.info("image_done forward failed (ws gone?): %s", e)
            return
    finally:
        try:
            await bus.unsubscribe(queue)
        except Exception:  # noqa: BLE001
            pass


async def forward_image_receipts(
    websocket: WebSocket, app_state: Any, *,
    receipts: list[dict] | list[Any],
    device_id: str,
) -> None:
    """For each `image_render` receipt in the turn, send a pending
    bubble + spawn a background watcher that swaps in the rendered
    media when it's ready.

    Called from `_hive_turn` immediately after `coord.coordinate()`
    returns. Errors are logged + swallowed — never propagate, the
    image bridge is a UX nicety on top of the turn's main reply.
    """
    bus = app_state.event_bus
    if bus is None:
        return
    recent_images = app_state.recent_images
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        if receipt.get("verb") != "image_render" or not receipt.get("ok"):
            continue
        payload = receipt.get("payload") or {}
        job_id = payload.get("job_id")
        prompt = payload.get("prompt") or ""
        if not job_id:
            continue
        if recent_images is not None:
            try:
                recent_images.record(
                    device_id=device_id, bot="hive",
                    job_id=job_id, prompt=prompt,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("recent_images record failed: %s", e)
        try:
            await websocket.send_json({
                "type": "image_pending",
                "job_id": job_id, "prompt": prompt,
            })
        except Exception as e:  # noqa: BLE001
            log.warning("image_pending send failed: %s", e)
            continue
        track_background_task(
            app_state,
            asyncio.create_task(
                watch_image_done_and_forward(websocket, bus, job_id),
                name=f"image_watch:{job_id}",
            ),
        )
