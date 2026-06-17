"""End-to-end test of the research → vault-learn → memory loop.

Sends Terry three turns over the same paired session:

  1. RESEARCH: ask her to research the Drake Cutlass Black ship
     and remember what she finds. Expects researcher delegation +
     vault writes.

  2. WAIT: short pause so the summarizer (M5.2) can persist the
     conversation digest if it triggers.

  3. QUIZ: ask her about the Drake Cutlass Black without giving any
     fresh context. To pass, the reply must reference content that
     came from the prior research turn (cargo capacity, manufacturer,
     role, etc.) rather than a generic "I don't know".

Reports per-turn flags + a final QUIZ_PASS/QUIZ_FAIL verdict based on
keyword presence in the second reply.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import Any

import httpx
import websockets


_GW = "http://127.0.0.1:8766"
_WS = "ws://127.0.0.1:8766"


# Substring set the QUIZ reply should mention to count as a pass. Drake
# Cutlass info that should surface from any half-decent research run.
_QUIZ_KEYWORDS = [
    "cutlass", "drake",
]
_QUIZ_BONUS = [
    "multi-role", "multi role", "exploration", "cargo",
    "smuggling", "pirate", "interdiction", "interdictor",
    "black", "blue", "red",         # variant names
    "manufactur",                   # manufacturer / manufactured
    "size", "tons", "scu",          # cargo capacity unit
]


async def _pair() -> str:
    async with httpx.AsyncClient(base_url=_GW, timeout=30) as h:
        c = (await h.get("/v1/pair/new")).json()["code"]
        r = await h.post("/v1/pair", json={
            "code": c, "name": "research-quiz", "platform": "test",
        })
        return r.json()["token"]


async def _drive(token: str, prompt: str, label: str) -> dict[str, Any]:
    """Drive one turn over chat WS; return rich summary."""
    out: dict[str, Any] = {
        "label": label, "prompt": prompt,
        "delegations": [], "helper_errors": [],
        "actions_ok": [], "actions_fail": [],
        "reply": "", "wall_s": 0.0, "events": [],
    }
    t0 = time.time()
    url = f"{_WS}/v1/chat/terry?token={token}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "user", "text": prompt}))
        deadline = t0 + 300
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=300)
            except asyncio.TimeoutError:
                out["events"].append("timeout")
                break
            ev = json.loads(raw)
            t = ev.get("type")
            out["events"].append(t)
            if t == "thought":
                out["delegations"] = [
                    d.get("role") for d in ev.get("delegations", [])
                ]
            elif t == "helper_reply" and ev.get("error"):
                out["helper_errors"].append(
                    f"{ev.get('role','?')}: {(ev['error'] or '')[:80]}"
                )
            elif t == "synthesis":
                for a in ev.get("actions") or []:
                    if not isinstance(a, dict) or "ok" not in a:
                        continue
                    if a.get("ok"):
                        out["actions_ok"].append(a.get("verb", "?"))
                    else:
                        out["actions_fail"].append(
                            f"{a.get('verb','?')}: {(a.get('detail') or '')[:80]}"
                        )
            elif t == "assistant":
                out["reply"] = ev.get("text", "")
            elif t == "done":
                break
    out["wall_s"] = round(time.time() - t0, 1)
    return out


def _safe(s: str) -> str:
    """Replace chars Windows cp1252 console can't render."""
    try:
        s.encode("cp1252")
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode("ascii", "replace").decode()


def _print_turn(t: dict) -> None:
    print(f"\n=== {t['label']}: {_safe(t['prompt'])!r}", flush=True)
    print(f"  delegations: {t['delegations']}", flush=True)
    print(f"  actions ok:  {t['actions_ok']}", flush=True)
    if t["actions_fail"]:
        print(f"  actions fail:", flush=True)
        for a in t["actions_fail"]:
            print(f"    - {_safe(a)}", flush=True)
    if t["helper_errors"]:
        print(f"  helper errors:", flush=True)
        for e in t["helper_errors"]:
            print(f"    - {_safe(e)}", flush=True)
    print(f"  reply: {_safe(t['reply'])[:400]}", flush=True)
    print(f"  wall:  {t['wall_s']}s", flush=True)


def _quiz_score(reply: str) -> tuple[int, list[str]]:
    """How many quiz keywords (lowercased) are in the reply?"""
    lower = reply.lower()
    hits_required = [kw for kw in _QUIZ_KEYWORDS if kw in lower]
    hits_bonus = [kw for kw in _QUIZ_BONUS if kw in lower]
    return (len(hits_required), hits_required + hits_bonus)


async def main() -> int:
    print(">> Pairing fresh session...", flush=True)
    token = await _pair()
    print(f">> Token: {token[:8]}...", flush=True)

    research_prompt = (
        "Please research the Drake Cutlass Black ship from Star Citizen — "
        "what role it's designed for, who manufactures it, and any notable "
        "specs. Save what you learn so I can ask you about it later."
    )
    research = await _drive(token, research_prompt, "RESEARCH")
    _print_turn(research)

    print(f"\n>> Sleeping 4s to let any async summarizer settle...", flush=True)
    await asyncio.sleep(4)

    quiz_prompt = (
        "Now: tell me about the Drake Cutlass Black. Use what you researched."
    )
    quiz = await _drive(token, quiz_prompt, "QUIZ")
    _print_turn(quiz)

    required, all_hits = _quiz_score(quiz["reply"])
    quiz_pass = required >= len(_QUIZ_KEYWORDS) and len(all_hits) >= 4

    print("\n" + "=" * 60, flush=True)
    print("VERDICT", flush=True)
    print("=" * 60, flush=True)
    print(f"  Research delegations: {research['delegations']}", flush=True)
    print(f"  Research vault writes: "
          f"{[a for a in research['actions_ok'] if 'vault' in a or 'learn' in a]}",
          flush=True)
    print(f"  Quiz reply length: {len(quiz['reply'])} chars", flush=True)
    print(f"  Quiz keyword hits ({len(all_hits)}): {all_hits}", flush=True)
    if quiz_pass:
        print("  QUIZ_PASS: Terry recalled material from her research turn.",
              flush=True)
    else:
        print("  QUIZ_FAIL: Quiz reply lacks expected research content.",
              flush=True)
        print("    (Memory + research loop is broken or under-tuned.)",
              flush=True)
    return 0 if quiz_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
