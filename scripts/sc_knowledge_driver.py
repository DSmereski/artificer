"""Drive a long Star Citizen knowledge-base conversation via the
gateway WebSocket, as a user would, asking Terry to remember each
answer to the vault. Auto-links happen on the vault_learn side via
_autolink_body.

Usage:
    python scripts/sc_knowledge_driver.py
        [--host http://127.0.0.1:8766] [--name sc-kb]
        [--limit N] [--out C:/tmp/ai-team/sc_kb_run.json]

The script pairs a fresh device automatically — no token needed up
front. Pass --token <hex> to reuse an existing device.
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

# Terry's replies routinely contain emoji and em-dashes. The Windows
# default cp1252 stdout chokes on those mid-run (UnicodeEncodeError).
# Force utf-8 so the driver survives any reply content.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# Each entry is one user-style chat turn. The phrasing asks Terry to
# answer + save to the vault so vault_learn fires and auto-linking
# wires neighbours together. Short, conversational, like a real user.
SCRIPT: list[str] = [
    "Hey Terry, I want to build a Star Citizen knowledge base in my vault. For each thing I ask, give me a tight answer and save a note to the vault so we can cross-link them later. Start by saving a top-level note: 'Star Citizen — overview' covering what the game is, the developer (Cloud Imperium / Roberts Space Industries), the persistent universe concept, and Squadron 42 as the linked single-player. Save it under category knowledge.",
    "Now save a note 'UEE — United Empire of Earth' covering the dominant human government, its capital Earth, the Messer era, and the modern Imperator system.",
    "Save a note 'Stanton system' — single-star system, four super-Earth planets owned by megacorporations: Hurston (Hurston Dynamics), Crusader (Crusader Industries), ArcCorp, microTech.",
    "Save a note 'Pyro system' — lawless system, recently opened, Pyro I-VI plus the Ruin Station hub, gang territory (Headhunters, XenoThreat, Frontier Fighters).",
    "Save 'Hurston' — first super-Earth in Stanton, owned by Hurston Dynamics, capital city Lorville, moons Aberdeen, Arial, Magda, Ita.",
    "Save 'Crusader' — gas giant in Stanton, floating city Orison, moons Cellin, Daymar, Yela. GrimHex sits in Yela's asteroid belt.",
    "Save 'ArcCorp' — fully-industrialised planet in Stanton, city Area18, moons Lyria and Wala.",
    "Save 'microTech' — frozen super-Earth in Stanton, city New Babbage, moons Calliope, Clio, Euterpe.",
    "Save 'Ship manufacturer — RSI (Roberts Space Industries)' — flagship line includes Aurora, Constellation, Polaris, Galaxy. Founded in-universe by Chris Roberts' fictional historical analogue.",
    "Save 'Ship manufacturer — Aegis Dynamics' — military heritage, ships include Gladius, Avenger, Vanguard, Sabre, Retaliator, Hammerhead, Reclaimer, Idris.",
    "Save 'Ship manufacturer — Anvil Aerospace' — military, ships include Hornet line, Carrack, Valkyrie, Terrapin, Hawk, Liberator.",
    "Save 'Ship manufacturer — Drake Interplanetary' — rough-edged, affordable, ships include Cutlass line, Caterpillar, Buccaneer, Dragonfly, Corsair, Vulture, Kraken, Herald.",
    "Save 'Ship manufacturer — Origin Jumpworks' — luxury, ships include 300-series, 600i, 890 Jump, 100-series, 400i, X1.",
    "Save 'Ship manufacturer — MISC' — Xi'an-influenced, ships include Freelancer line, Starfarer, Reliant, Prospector, Hull-series, Endeavor.",
    "Save 'Ship manufacturer — Crusader Industries' — civilian transport, ships include Mercury Star Runner, Genesis Starliner, Ares Inferno/Ion, A1 Spirit, A2 Hercules, C2/M2.",
    "Save 'Ship role — Combat' — single-seat fighters (Gladius, Hornet F7C, Arrow, Sabre), multi-crew (Vanguard, Eclipse), corvette+ (Hammerhead, Polaris, Idris).",
    "Save 'Ship role — Cargo hauling' — small (Cutlass Black, Freelancer), medium (Caterpillar, C2 Hercules), large (Hull C, Hull D, M2 Hercules).",
    "Save 'Ship role — Mining' — handheld Greycat Pyro PYT pistol, ROC ground vehicle, ship (Prospector solo, MOLE multi-head, Argo Mole mid-size, Orion industrial).",
    "Save 'Ship role — Salvage' — Drake Vulture (solo), Aegis Reclaimer (industrial), salvage targets are hull strips converted to RMC (Recycled Material Composite).",
    "Save 'Ship role — Exploration' — Anvil Carrack (flagship explorer with onboard medical, ROC bay, snub fighter), Origin 600i Explorer, MISC Endeavor with science modules.",
    "Save 'Ship role — Medical' — RSI Apollo (Triage/Medivac), Drake Cutlass Red, Carrack medbed, Origin 890 Jump medbay. Medical beds tier T1/T2/T3 control respawn rights.",
    "Save 'Gameplay — Quantum travel' — in-system FTL using quantum drive, consumes quantum fuel, requires Line of Sight to destination, can be interdicted by hostile players using a Mantis or Cutlass Blue.",
    "Save 'Gameplay — Refueling and repair' — Starfarer (MISC) hauls hydrogen+quantum fuel, Crucible repairs hulls, Vulcan does fuel+repair+rearm in one ship.",
    "Save 'Faction — Vanduul' — hostile alien race, raiders, primary antagonist in Squadron 42, Vanduul Scythe and Blade fighters seen in-game.",
    "Save 'Faction — Banu' — peaceful trading alien race, Banu Defender ship is player-flyable.",
    "Save 'Faction — Xi'an' — diplomatic alien race, technologically advanced, ships Khartu-al and Nox influenced by their tech.",
    "Save 'Faction — Nine Tails' — human pirate organisation, common low-tier enemy in bounty contracts around Stanton.",
    "Save 'Squadron 42' — single-player military campaign featuring Mark Hamill (Cdr Steve Colton), Gary Oldman (Adm Bishop), Gillian Anderson, Henry Cavill, Mark Strong. Linked to the persistent universe via shared characters.",
    "Save 'Locations — Lorville' — capital city of Hurston, dystopian company town under Hurston Dynamics, key landing zone for player ships.",
    "Save 'Locations — Area18' — ArcCorp capital, neon-lit cyberpunk district, hub for ship dealers and a frequent mission start.",
    "Save 'Locations — New Babbage' — microTech capital, snowy tech-city, home of microTech Headquarters and the Aspire Grand hotel.",
    "Now search the vault for everything tagged with 'star-citizen' or under category knowledge that mentions Stanton, and tell me which notes you can find. Cross-link any you find that aren't already connected.",
    "Final: summarise everything we just saved in 3 bullets so I can confirm coverage is complete.",
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


async def drive(
    host: str, token: str, out_path: Path, limit: int | None,
) -> None:
    ws_host = host.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{ws_host}/v1/chat/terry"
    headers = {"Authorization": f"Bearer {token}"}
    transcript: list[dict] = []
    script = SCRIPT if limit is None else SCRIPT[:limit]

    async with websockets.connect(
        url, additional_headers=headers, max_size=2**22,
    ) as ws:
        for turn_idx, user_text in enumerate(script, 1):
            print(f"\n=== TURN {turn_idx}/{len(script)}: USER ===")
            print(user_text[:140] + ("…" if len(user_text) > 140 else ""))
            await ws.send(json.dumps({"type": "user", "text": user_text}))
            transcript.append({
                "turn": turn_idx, "role": "user",
                "text": user_text, "ts": time.time(),
            })

            assistant_chunks: list[str] = []
            events: list[dict] = []
            issues: list[str] = []
            t0 = time.time()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=360)
                except asyncio.TimeoutError:
                    issues.append(f"timeout after {time.time()-t0:.1f}s")
                    events.append({"type": "_timeout"})
                    break
                msg = json.loads(raw)
                events.append(msg)
                t = msg.get("type")
                if t == "assistant":
                    assistant_chunks.append(msg.get("text", ""))
                elif t == "done":
                    break
                elif t == "error":
                    issues.append(f"error frame: {msg.get('message','?')}")
                    break
                elif t == "system_notice":
                    print(f"  [system_notice] {msg.get('text','')[:120]}")
            full_reply = "".join(assistant_chunks).strip()
            elapsed = time.time() - t0
            verbs = [
                e.get("verb") for e in events
                if e.get("type") == "action_done" and e.get("verb")
            ]
            print(
                f"--- TERRY ({elapsed:.1f}s, {len(events)} ev, "
                f"actions={verbs}) ---"
            )
            print((full_reply or "(empty)")[:500])
            if issues:
                print(f"  ! ISSUES: {issues}")
            transcript.append({
                "turn": turn_idx, "role": "assistant",
                "text": full_reply, "events": events,
                "issues": issues, "actions": verbs,
                "elapsed_s": round(elapsed, 2), "ts": time.time(),
            })
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(transcript, indent=2), encoding="utf-8",
            )
            await asyncio.sleep(0.5)

    print(f"\nTranscript saved to: {out_path}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--name", default="sc-kb")
    p.add_argument("--token", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default="C:/tmp/ai-team/sc_kb_run.json")
    args = p.parse_args(argv)

    async def _run():
        token = args.token or await pair_device(args.host, args.name)
        if not args.token:
            print(f"paired new device, token={token[:16]}…")
        await drive(args.host, token, Path(args.out), args.limit)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
