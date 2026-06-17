"""One-shot: re-drive the SC ship-role-cargo turn. Runs after the
synth-stub override fix. Previous attempts had synth emit a slug-only
vault_learn that silently failed; the derive override now fills in."""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROMPT = (
    "Save 'Ship role — Cargo hauling' — small (Cutlass Black, Freelancer), "
    "medium (Caterpillar, C2 Hercules), large (Hull C, Hull D, M2 Hercules)."
)
HOST = "http://127.0.0.1:8766"


async def pair() -> str:
    async with httpx.AsyncClient(base_url=HOST, timeout=10.0) as c:
        code = (await c.get("/v1/pair/new")).json()["code"]
        return (await c.post(
            "/v1/pair",
            json={"code": code, "name": "sc-cargo", "platform": "py-driver"},
        )).json()["token"]


async def main() -> None:
    token = await pair()
    ws_url = HOST.replace("http://", "ws://") + "/v1/chat/terry"
    async with websockets.connect(
        ws_url, additional_headers={"Authorization": f"Bearer {token}"},
        max_size=2**22,
    ) as ws:
        print(f"> {PROMPT}")
        await ws.send(json.dumps({"type": "user", "text": PROMPT}))
        t0 = time.time()
        actions: list[str] = []
        reply_parts: list[str] = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=300.0)
            except asyncio.TimeoutError:
                print("!! timeout")
                break
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "assistant":
                reply_parts.append(ev.get("text", ""))
            elif t == "action_done":
                v = ev.get("verb")
                if v:
                    actions.append(v)
            elif t == "done":
                break
        print(f"\n--- DONE {time.time()-t0:.1f}s actions={actions} ---")
        print("".join(reply_parts)[:500])


asyncio.run(main())
