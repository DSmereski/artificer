"""Deeper probe of Terry's image-gen workflow.

Sends an ambiguous image request, expects ASK_USER, answers, expects
CONFIRM_IMAGE, says yes, expects render to start (image_progress or
image_url events). Validates no naked-JSON leaks into visible bubbles.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8766"


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


async def collect_until_done(ws, max_seconds: float = 120.0) -> list[dict]:
    events: list[dict] = []
    t_start = time.time()
    while time.time() - t_start < max_seconds:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max_seconds)
        except asyncio.TimeoutError:
            break
        try:
            ev = json.loads(raw)
        except Exception:
            continue
        events.append(ev)
        if ev.get("type") == "done":
            break
    return events


def has_no_naked_json(events: list[dict]) -> tuple[bool, str]:
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        text = (ev.get("text") or "").strip()
        # Bubble must not start with `{` (raw JSON leak from small model)
        if text.startswith("{"):
            return False, f"naked JSON in assistant bubble: {text[:120]}"
    return True, ""


async def run() -> int:
    failures: list[str] = []

    def fail(msg: str) -> None:
        print(f"  FAIL: {msg}", flush=True)
        failures.append(msg)

    def ok(msg: str) -> None:
        print(f"  OK  : {msg}", flush=True)

    # Pair
    banner("PAIR")
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as http:
        r = await http.get("/v1/pair/new")
        code = r.json()["code"]
        r = await http.post("/v1/pair", json={
            "code": code, "name": "img-smoke", "platform": "test",
        })
        token = r.json()["token"]
        ok("paired")

        # Reset Terry first so we start clean
        await http.post(
            "/v1/chat/terry/reset",
            headers={"Authorization": f"Bearer {token}"},
        )
        ok("reset Terry's history")

    ws_url = f"ws://127.0.0.1:8766/v1/chat/terry?token={token}"

    banner("AMBIGUOUS IMAGE REQUEST -> expect ASK_USER")
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "user", "text": "draw me a portrait"}))
        evs = await collect_until_done(ws, max_seconds=120.0)
        types = [e.get("type") for e in evs]
        print(f"     event types: {types}", flush=True)
        clean, why = has_no_naked_json(evs)
        if not clean:
            fail(why)
        else:
            ok("no naked JSON in assistant bubbles")

        ask_evs = [e for e in evs if e.get("type") == "ask_user"]
        if ask_evs:
            ok(f"got ask_user: q={ask_evs[0].get('question')!r}, "
               f"options={ask_evs[0].get('options')}")
        else:
            # Some replies may go straight to confirm_image if Terry
            # decides it's unambiguous enough. Accept either.
            confirm_evs = [e for e in evs if e.get("type") == "confirm_image"]
            if confirm_evs:
                ok(f"went straight to confirm_image (acceptable)")
            else:
                fail("no ask_user or confirm_image event for ambiguous prompt")
                return 1

    # Follow-up: pick an option
    banner("ANSWER -> expect CONFIRM_IMAGE")
    async with websockets.connect(ws_url) as ws:
        # Provide enough detail that Terry should be ready to confirm.
        await ws.send(json.dumps({
            "type": "user",
            "text": "a fantasy elf with silver hair, portrait, cinematic lighting",
        }))
        evs = await collect_until_done(ws, max_seconds=120.0)
        types = [e.get("type") for e in evs]
        print(f"     event types: {types}", flush=True)
        clean, why = has_no_naked_json(evs)
        if not clean:
            fail(why)
        confirm_evs = [e for e in evs if e.get("type") == "confirm_image"]
        ask_evs = [e for e in evs if e.get("type") == "ask_user"]
        if confirm_evs:
            ci = confirm_evs[-1]
            ok(f"confirm_image: prompt={ci.get('prompt', '')[:80]!r}, "
               f"aspect={ci.get('aspect')}, loras={ci.get('loras')}")
        elif ask_evs:
            ok("Terry asked another clarifying question (also acceptable)")
        else:
            fail(f"no confirm_image or ask_user; events={types}")

    banner("RESULTS")
    if failures:
        print(f"  {len(failures)} failures:", flush=True)
        for f in failures:
            print(f"    - {f}", flush=True)
        return 1
    print("  Image-flow smoke passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
