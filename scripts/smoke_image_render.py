"""Walk Terry through the FULL image-gen flow on one WS connection.

The pending_image_confirms map is per-device but cleared on WS
disconnect. So to walk through ASK_USER -> CONFIRM_IMAGE -> render
we need one persistent connection.

Saying "yes" only renders if there's a pending CONFIRM_IMAGE.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8766"


async def collect(ws, label: str, max_seconds: float = 180.0) -> list[dict]:
    print(f"\n  --- collecting events for: {label} ---", flush=True)
    events: list[dict] = []
    t_start = time.time()
    while time.time() - t_start < max_seconds:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max_seconds)
        except asyncio.TimeoutError:
            print("  (timeout)", flush=True)
            break
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        events.append(ev)
        t = ev.get("type")
        # Concise event log
        if t == "assistant":
            txt = (ev.get("text") or "").replace("\n", " ")[:120]
            print(f"  [assistant] {txt}", flush=True)
        elif t == "ask_user":
            print(f"  [ask_user]  q={ev.get('question')!r}  "
                  f"opts={ev.get('options')}", flush=True)
        elif t == "confirm_image":
            print(f"  [confirm]   prompt={ev.get('prompt', '')[:80]!r}  "
                  f"aspect={ev.get('aspect')}  loras={ev.get('loras')}", flush=True)
        elif t == "image_progress":
            print(f"  [progress]  pct={ev.get('pct')}", flush=True)
        elif t == "image_url":
            print(f"  [image]     url={ev.get('url')}", flush=True)
        elif t == "image_error":
            print(f"  [imgerr]    {ev.get('error')}", flush=True)
        elif t == "done":
            print(f"  [done]", flush=True)
            break
        else:
            print(f"  [{t}] {ev}", flush=True)
    return events


async def run() -> int:
    failures: list[str] = []

    # Pair fresh
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as http:
        r = await http.get("/v1/pair/new")
        code = r.json()["code"]
        r = await http.post("/v1/pair", json={
            "code": code, "name": "img-render", "platform": "test",
        })
        token = r.json()["token"]
        await http.post(
            "/v1/chat/terry/reset",
            headers={"Authorization": f"Bearer {token}"},
        )
        print(f"paired + reset (token len={len(token)})", flush=True)

    ws_url = f"ws://127.0.0.1:8766/v1/chat/terry?token={token}"
    async with websockets.connect(ws_url) as ws:
        # Turn 1 — ambiguous
        await ws.send(json.dumps({
            "type": "user",
            "text": "draw me a portrait of a fantasy elf, silver hair, "
                    "portrait orientation, cinematic lighting. just go.",
        }))
        e1 = await collect(ws, "turn 1: detailed prompt")

        confirm = next(
            (e for e in e1 if e.get("type") == "confirm_image"), None,
        )
        ask = next(
            (e for e in e1 if e.get("type") == "ask_user"), None,
        )
        if confirm is None and ask is None:
            failures.append("turn 1: no confirm_image or ask_user")

        # If Terry asked instead of confirming, answer with full detail
        # and try again.
        attempts = 0
        while confirm is None and attempts < 3:
            attempts += 1
            await ws.send(json.dumps({
                "type": "user",
                "text": "any aspect, any pose, any LoRA — just confirm and "
                        "render. portrait, cinematic, detailed.",
            }))
            evs = await collect(ws, f"turn {attempts+1}: pushing for CONFIRM")
            confirm = next(
                (e for e in evs if e.get("type") == "confirm_image"), None,
            )

        if confirm is None:
            failures.append(
                f"could not get Terry to CONFIRM_IMAGE after "
                f"{attempts+1} turns",
            )
            print("\nFAIL: " + " | ".join(failures), flush=True)
            return 1

        # Now say yes — should trigger render
        print("\n  >>> sending 'yes' to confirm <<<", flush=True)
        await ws.send(json.dumps({"type": "user", "text": "yes"}))
        evs = await collect(ws, "after yes", max_seconds=240.0)

        if any(e.get("type") == "image_url" for e in evs):
            print("\n  PASS: image rendered end-to-end", flush=True)
            return 0
        if any(e.get("type") == "image_progress" for e in evs):
            print("\n  PASS: render started "
                  "(image_progress events seen)", flush=True)
            return 0
        if any(e.get("type") == "image_error" for e in evs):
            err = next(e for e in evs if e.get("type") == "image_error")
            print(f"\n  Render error: {err}", flush=True)
            failures.append(f"render error: {err.get('error')}")
            return 1
        failures.append("no image_progress/image_url/image_error after yes")

    print("\nFAIL: " + " | ".join(failures), flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
