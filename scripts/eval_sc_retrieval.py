"""Phase-1 retrieval-quality eval: ask Terry questions about the 31
SC notes we just landed. For each turn, capture:
  - did librarian/researcher run + hit
  - reply text
  - emitted actions
  - elapsed wall-clock

Then a verdict step (offline) compares each reply against ground truth
expected substrings + checks that no hallucination patterns appear.

Run:
    python scripts/eval_sc_retrieval.py [--out C:/tmp/ai-team/sc_retrieval.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# Each entry: (id, prompt, list_of_expected_substrings).
# Substrings are case-insensitive; ANY match counts as a pass.
QUESTIONS: list[tuple[str, str, list[str]]] = [
    ("Q01_overview",
     "What is Star Citizen and who develops it?",
     ["Cloud Imperium", "Roberts Space Industries", "persistent universe"]),
    ("Q02_uee",
     "What is the UEE?",
     ["United Empire of Earth", "Messer", "Imperator"]),
    ("Q03_stanton_planets",
     "Which planets are in the Stanton system?",
     ["Hurston", "Crusader", "ArcCorp", "microTech"]),
    ("Q04_pyro_gangs",
     "What gangs operate in Pyro?",
     ["Headhunters", "XenoThreat", "Frontier Fighters"]),
    ("Q05_hurston_moons",
     "List Hurston's moons.",
     ["Aberdeen", "Arial", "Magda", "Ita"]),
    ("Q06_crusader_city",
     "What's the main city on Crusader?",
     ["Orison"]),
    ("Q07_arccorp_city",
     "Which city is on ArcCorp?",
     ["Area18"]),
    ("Q08_microtech_city",
     "Where do you land on microTech?",
     ["New Babbage"]),
    ("Q09_rsi_ships",
     "What ships does RSI make?",
     ["Aurora", "Constellation", "Polaris", "Galaxy"]),
    ("Q10_aegis_ships",
     "Name some Aegis Dynamics ships.",
     ["Gladius", "Vanguard", "Hammerhead", "Idris"]),
    ("Q11_anvil_ships",
     "Which manufacturer makes the Carrack?",
     ["Anvil"]),
    ("Q12_drake_ships",
     "Tell me about Drake Interplanetary's lineup.",
     ["Cutlass", "Caterpillar", "Vulture", "Kraken"]),
    ("Q13_origin_luxury",
     "What's Origin Jumpworks known for?",
     ["luxury", "890 Jump", "600i"]),
    ("Q14_misc_ships",
     "Which ships does MISC make?",
     ["Freelancer", "Starfarer", "Prospector", "Hull"]),
    ("Q15_crusader_industries",
     "What does Crusader Industries make?",
     ["Mercury", "Star Runner", "Hercules", "Genesis"]),
    ("Q16_combat_fighters",
     "What are common single-seat combat fighters?",
     ["Gladius", "Hornet", "Arrow", "Sabre"]),
    ("Q17_cargo_size_tiers",
     "How does cargo hauling tier by ship size?",
     ["Cutlass Black", "Freelancer", "Hercules", "Hull"]),
    ("Q18_mining_ships",
     "Which ships are used for mining?",
     ["Prospector", "MOLE", "Orion"]),
    ("Q19_salvage_ships",
     "What ships are best for salvage?",
     ["Vulture", "Reclaimer"]),
    ("Q20_exploration_ship",
     "What's the flagship exploration ship?",
     ["Carrack"]),
    ("Q21_medical_ships",
     "Name medical ships.",
     ["Apollo", "Cutlass Red", "890 Jump"]),
    ("Q22_quantum_travel",
     "How does quantum travel work?",
     ["quantum drive", "Line of Sight", "interdict"]),
    ("Q23_refueling_ships",
     "Which ship hauls fuel?",
     ["Starfarer"]),
    ("Q24_factions_vanduul",
     "Are Vanduul friendly?",
     ["hostile", "raiders", "antagonist"]),
    ("Q25_factions_banu",
     "Are the Banu peaceful?",
     ["peaceful", "trading"]),
    ("Q26_factions_xian",
     "Tell me about the Xi'an.",
     ["diplomatic", "Khartu-al", "Nox"]),
    ("Q27_pirates",
     "What human pirate organisation is common in Stanton?",
     ["Nine Tails"]),
    ("Q28_squadron42_cast",
     "Who's in the Squadron 42 cast?",
     ["Mark Hamill", "Gary Oldman", "Henry Cavill"]),
    ("Q29_lorville_owner",
     "Who owns Lorville?",
     ["Hurston Dynamics"]),
    ("Q30_synthesis_alien_factions",
     "Which factions are alien races?",
     ["Vanduul", "Banu", "Xi'an"]),
    ("Q31_synthesis_combat_corvette",
     "What corvette-class combat ships exist?",
     ["Hammerhead", "Polaris", "Idris"]),
]


# Hallucination triggers — if reply matches ANY, flag it. These are
# patterns we know planner-qwen has fabricated under retrieval pressure.
HALLUCINATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("invented-source", re.compile(r"\b(?:wiki|website|page|article)\s+(?:says|states|claims)", re.IGNORECASE)),
    ("fake-citation", re.compile(r"according\s+to\s+(?:the\s+)?(?:official|developer)", re.IGNORECASE)),
    ("hedging-no-source", re.compile(r"\bi\s+(?:think|believe|recall|seem\s+to\s+remember)\b", re.IGNORECASE)),
    ("made-up-spec", re.compile(r"\b\d{2,4}\s*(?:m/s|km/s|SCU|aUEC|UEC)\b", re.IGNORECASE)),
    ("unrelated-ship", re.compile(r"\b(?:Cutlass\s+(?:Pink|Yellow|Orange))\b", re.IGNORECASE)),  # obvious make-up
]

REFUSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"i\s+couldn'?t\s+find\s+that\s+in\s+your\s+vault", re.IGNORECASE),
    re.compile(r"i\s+(?:had\s+)?trouble\s+planning", re.IGNORECASE),
    re.compile(r"i\s+couldn'?t\s+polish\s+this", re.IGNORECASE),
    re.compile(r"couldn'?t\s+compose\s+a\s+clean\s+reply", re.IGNORECASE),
]


async def pair(host: str, name: str) -> str:
    async with httpx.AsyncClient(base_url=host, timeout=10.0) as c:
        code = (await c.get("/v1/pair/new")).json()["code"]
        return (await c.post(
            "/v1/pair",
            json={"code": code, "name": name, "platform": "py-driver"},
        )).json()["token"]


async def drive(host: str, token: str, out: Path) -> None:
    ws_url = host.replace("http://", "ws://") + "/v1/chat/terry"
    transcript: list[dict] = []
    async with websockets.connect(
        ws_url, additional_headers={"Authorization": f"Bearer {token}"},
        max_size=2**22,
    ) as ws:
        for qid, prompt, expected in QUESTIONS:
            print(f"\n=== {qid} ===\n> {prompt}")
            await ws.send(json.dumps({"type": "user", "text": prompt}))
            t0 = time.time()
            reply_parts: list[str] = []
            actions: list[str] = []
            helpers: list[str] = []
            librarian_hits: list[dict] = []
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=240.0)
                except asyncio.TimeoutError:
                    print("  !! timed out")
                    break
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "assistant":
                    reply_parts.append(ev.get("text", ""))
                elif t == "done":
                    break
                elif t == "delegate":
                    helpers.append(ev.get("role", ""))
                elif t == "helper_reply":
                    role = ev.get("role", "")
                    if role == "librarian":
                        librarian_hits.append({
                            "plan": ev.get("plan", []),
                            "facts": ev.get("facts", []),
                            "hits": ev.get("hits", []),
                        })
                elif t == "action_done":
                    v = ev.get("verb")
                    if v:
                        actions.append(v)
                elif t == "error":
                    print(f"  !! error: {ev.get('message','?')}")
                    break
            dt = time.time() - t0
            reply = "".join(reply_parts).strip()
            print(f"  {dt:.1f}s helpers={helpers} actions={actions}")
            print(f"  reply[:220]: {reply[:220]}")
            transcript.append({
                "id": qid, "prompt": prompt, "expected": expected,
                "reply": reply, "actions": actions, "helpers": helpers,
                "librarian_hits": librarian_hits, "elapsed_s": dt,
            })
    out.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"\ntranscript -> {out}")


def grade(transcript_path: Path) -> None:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    total = len(data)
    passed = 0
    failed: list[tuple[str, str]] = []
    refusals: list[str] = []
    hallucinations: list[tuple[str, str]] = []
    no_librarian: list[str] = []
    for t in data:
        qid = t["id"]
        reply = t.get("reply", "") or ""
        expected = t.get("expected", [])
        librarian_hits = t.get("librarian_hits", [])

        # Refusal flag (informational only — substring check still runs
        # because the P1.2 grounded fallback appends real hits AFTER the
        # "couldn't polish" preamble, so the reply IS useful).
        if any(p.search(reply) for p in REFUSAL_PATTERNS):
            refusals.append(qid)

        # Expected-substring check (case-insensitive ANY).
        lr = reply.lower()
        hits = [e for e in expected if e.lower() in lr]
        if not hits:
            failed.append((qid, f"missing all of {expected}"))
        else:
            missing = [e for e in expected if e.lower() not in lr]
            if len(hits) >= max(1, len(expected) // 2):
                passed += 1
            else:
                failed.append((qid, f"only matched {hits}, missed {missing}"))

        # Librarian-hit check.
        if not librarian_hits:
            no_librarian.append(qid)

        # Hallucination patterns.
        for name, pat in HALLUCINATION_PATTERNS:
            if pat.search(reply):
                hallucinations.append((qid, name))

    print(f"\n=== GRADE === passed {passed}/{total}")
    if failed:
        print(f"\nfailed ({len(failed)}):")
        for q, r in failed:
            print(f"  {q}: {r}")
    if refusals:
        print(f"\nrefusals ({len(refusals)}): {refusals}")
    if no_librarian:
        print(f"\nturns without librarian hit ({len(no_librarian)}): {no_librarian}")
    if hallucinations:
        print(f"\nhallucination flags ({len(hallucinations)}):")
        for q, n in hallucinations:
            print(f"  {q}: {n}")


async def _run() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--out", default="C:/tmp/ai-team/sc_retrieval.json")
    p.add_argument("--token", default=None)
    p.add_argument("--name", default="sc-retrieval")
    p.add_argument("--grade-only", action="store_true",
                   help="Skip driving; only grade the existing transcript.")
    args = p.parse_args()
    out = Path(args.out)
    if not args.grade_only:
        token = args.token or await pair(args.host, args.name)
        await drive(args.host, token, out)
    grade(out)


if __name__ == "__main__":
    asyncio.run(_run())
