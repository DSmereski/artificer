"""Battery of realistic chat prompts — exercises every typical app flow
through the live HiveCoordinator and reports what went wrong per turn.

Designed to surface real conversational issues so they can be fixed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx
import websockets


PROMPTS = [
    # 1. Greeting
    ("greet", "hi who are you?"),
    # 2. Casual question (no helpers needed)
    ("casual", "thanks. how's it going?"),
    # 3. System status (sysmon)
    ("sysmon", "what's the gpu temp"),
    # 4. Simple coding task
    ("coder", "write me a python one-liner that reverses a string"),
    # 5. Research with corroboration
    ("research", "research what a Drake Cutlass is in Star Citizen"),
    # 6. Image gen request (image_director)
    ("image", "draw a portrait of a night elf with silver hair"),
    # 7. Memory write (vault_learn)
    ("remember", "remember that my favorite color is dark teal"),
    # 8. Multi-task
    ("multi", "what's the gpu temp and write a hello-world in rust"),
]


async def _drive(token: str, label: str, prompt: str) -> dict:
    """Run one turn, collect events, summarize what happened."""
    summary: dict = {
        "label": label, "prompt": prompt, "events": [],
        "delegations": [], "helper_errors": [], "synth_action_oks": [],
        "synth_action_fails": [], "final_reply": "",
        "wall_s": 0.0, "fallback": False, "blocked": False,
    }
    t0 = time.time()
    url = f"ws://127.0.0.1:8766/v1/chat/terry?token={token}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "user", "text": prompt}))
        deadline = t0 + 240
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=240)
            except asyncio.TimeoutError:
                summary["events"].append("ws_timeout")
                break
            ev = json.loads(raw)
            t_type = ev.get("type")
            summary["events"].append(t_type)
            if t_type == "thought":
                summary["delegations"] = [
                    d.get("role") for d in ev.get("delegations", [])
                ]
            elif t_type == "helper_reply":
                err = ev.get("error")
                if err:
                    summary["helper_errors"].append(
                        f"{ev['role']}: {err[:80]}"
                    )
            elif t_type == "synthesis":
                for a in ev.get("actions") or []:
                    if not isinstance(a, dict):
                        continue
                    if "ok" not in a:           # first synthesis (pre-execute)
                        continue
                    if a.get("ok"):
                        summary["synth_action_oks"].append(a.get("verb", "?"))
                    else:
                        summary["synth_action_fails"].append(
                            f"{a.get('verb','?')}: {a.get('detail','')[:80]}"
                        )
            elif t_type == "assistant":
                summary["final_reply"] = ev.get("text", "")
            elif t_type == "done":
                break
    summary["wall_s"] = round(time.time() - t0, 1)
    summary["fallback"] = "trouble planning" in summary["final_reply"].lower()
    return summary


async def main() -> int:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8766", timeout=30) as h:
        r = await h.get("/v1/pair/new")
        c = r.json()["code"]
        r = await h.post("/v1/pair", json={
            "code": c, "name": "prompt-battery", "platform": "test",
        })
        token = r.json()["token"]

    issues: list[str] = []
    for label, prompt in PROMPTS:
        print(f"\n--- {label}: {prompt!r}", flush=True)
        try:
            s = await _drive(token, label, prompt)
        except Exception as e:
            issues.append(f"{label}: ws crashed: {e}")
            print(f"  CRASH: {e}", flush=True)
            continue
        flags: list[str] = []
        if s["fallback"]:
            flags.append("PLANNER_FALLBACK")
        if s["helper_errors"]:
            flags.append(f"HELPER_ERR({','.join(e.split(':')[0] for e in s['helper_errors'])})")
        if s["synth_action_fails"]:
            flags.append(f"ACTION_FAIL({','.join(f.split(':')[0] for f in s['synth_action_fails'])})")
        if not s["final_reply"]:
            flags.append("EMPTY_REPLY")

        print(f"  delegations: {s['delegations']}", flush=True)
        print(f"  reply: {s['final_reply'][:200]}", flush=True)
        if s["helper_errors"]:
            print(f"  helper errors:", flush=True)
            for e in s["helper_errors"]:
                print(f"    - {e}", flush=True)
        if s["synth_action_oks"]:
            print(f"  actions ok: {s['synth_action_oks']}", flush=True)
        if s["synth_action_fails"]:
            print(f"  actions failed:", flush=True)
            for f in s["synth_action_fails"]:
                print(f"    - {f}", flush=True)
        print(f"  wall {s['wall_s']}s  flags: {flags or 'OK'}", flush=True)
        if flags:
            issues.append(f"{label}: {flags}")

    print("\n=== ISSUES ===", flush=True)
    if not issues:
        print("  None — all 8 prompts produced clean replies.", flush=True)
        return 0
    for i in issues:
        print(f"  - {i}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
