"""Re-drive only the SC knowledge-base turns that didn't land in the
prior run. Uses the same pair-then-WS-chat flow as sc_knowledge_driver
but cherry-picks the 14 missing prompts. Run after gateway restart so
the new _execute_user_save_fallback code path is active.

Usage:
    python scripts/sc_kb_missing.py
        [--host http://127.0.0.1:8766]
        [--out C:/tmp/ai-team/sc_missing.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


MISSING: list[str] = [
    "Save 'ArcCorp' — fully-industrialised planet in Stanton, city Area18, moons Lyria and Wala.",
    "Save 'microTech' — frozen super-Earth in Stanton, city New Babbage, moons Calliope, Clio, Euterpe.",
    "Save 'Ship manufacturer — Anvil Aerospace' — military, ships include Hornet line, Carrack, Valkyrie, Terrapin, Hawk, Liberator.",
    "Save 'Ship manufacturer — Drake Interplanetary' — rough-edged, affordable, ships include Cutlass line, Caterpillar, Buccaneer, Dragonfly, Corsair, Vulture, Kraken, Herald.",
    "Save 'Ship manufacturer — Origin Jumpworks' — luxury, ships include 300-series, 600i, 890 Jump, 100-series, 400i, X1.",
    "Save 'Ship role — Cargo hauling' — small (Cutlass Black, Freelancer), medium (Caterpillar, C2 Hercules), large (Hull C, Hull D, M2 Hercules).",
    "Save 'Ship role — Mining' — handheld Greycat Pyro PYT pistol, ROC ground vehicle, ship (Prospector solo, MOLE multi-head, Argo Mole mid-size, Orion industrial).",
    "Save 'Faction — Vanduul' — hostile alien race, raiders, primary antagonist in Squadron 42, Vanduul Scythe and Blade fighters seen in-game.",
    "Save 'Faction — Banu' — peaceful trading alien race, Banu Defender ship is player-flyable.",
    'Save "Faction — Xi\'an" — diplomatic alien race, technologically advanced, ships Khartu-al and Nox influenced by their tech.',
    "Save 'Faction — Nine Tails' — human pirate organisation, common low-tier enemy in bounty contracts around Stanton.",
    "Save 'Squadron 42' — single-player military campaign featuring Mark Hamill (Cdr Steve Colton), Gary Oldman (Adm Bishop), Gillian Anderson, Henry Cavill, Mark Strong. Linked to the persistent universe via shared characters.",
    "Save 'Locations — Lorville' — capital city of Hurston, dystopian company town under Hurston Dynamics, key landing zone for player ships.",
    "Save 'Locations — New Babbage' — microTech capital, snowy tech-city, home of microTech Headquarters and the Aspire Grand hotel.",
]


async def pair_device(host: str, name: str) -> str:
    async with httpx.AsyncClient(base_url=host, timeout=10.0) as c:
        r = await c.get("/v1/pair/new")
        r.raise_for_status()
        code = r.json()["code"]
        r2 = await c.post(
            "/v1/pair",
            json={"code": code, "name": name, "platform": "py-driver"},
        )
        r2.raise_for_status()
        return r2.json()["token"]


async def drive(host: str, token: str, out: Path) -> None:
    ws_host = host.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{ws_host}/v1/chat/terry"
    headers = {"Authorization": f"Bearer {token}"}
    transcript: list[dict] = []

    async with websockets.connect(url, additional_headers=headers) as ws:
        for i, prompt in enumerate(MISSING, 1):
            transcript.append({"turn": i, "role": "user", "text": prompt})
            print(f"\n=== TURN {i}/{len(MISSING)}: USER ===")
            print(prompt[:160])
            await ws.send(json.dumps({"type": "user", "text": prompt}))
            t0 = time.monotonic()
            events: list[dict] = []
            assistant_chunks: list[str] = []
            actions: list[dict] = []
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=300.0)
                except asyncio.TimeoutError:
                    print(f"!! turn {i} timed out")
                    break
                ev = json.loads(raw)
                events.append(ev)
                etype = ev.get("type")
                if etype == "assistant":
                    assistant_chunks.append(ev.get("text", ""))
                elif etype == "done":
                    break
                elif etype == "error":
                    print(f"!! error frame: {ev.get('message','?')}")
                    break
                elif etype == "action_done":
                    verb = ev.get("verb")
                    if verb:
                        actions.append({"verb": verb})
                elif etype == "synthesis":
                    for a in ev.get("actions", []):
                        if isinstance(a, dict) and a.get("verb"):
                            actions.append(a)
            full_reply = "".join(assistant_chunks).strip()
            dt = time.monotonic() - t0
            verbs = [a.get("verb") for a in actions]
            print(f"--- TERRY ({dt:.1f}s, {len(events)} ev, "
                  f"actions={verbs}) ---")
            print((full_reply or "(empty)")[:400])
            transcript.append({
                "turn": i, "role": "assistant",
                "text": full_reply, "events": events,
                "actions": actions, "elapsed_s": dt,
                "ts": time.time(),
            })

    out.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"\nTranscript saved to: {out}")


async def _run() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--out", default="C:/tmp/ai-team/sc_missing.json")
    p.add_argument("--token", default=None)
    p.add_argument("--name", default="sc-missing")
    args = p.parse_args()
    token = args.token or await pair_device(args.host, args.name)
    if not args.token:
        print(f"paired new device, token={token[:16]}…")
    await drive(args.host, token, Path(args.out))


if __name__ == "__main__":
    asyncio.run(_run())
