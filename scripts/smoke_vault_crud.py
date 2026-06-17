"""Walks Terry through a research → recall → update → remove flow
with human-shaped prompts. Single paired session so vault writes and
conversation memory persist across turns.

Reports per-turn helper dispatch, action verbs, latency, and a final
verdict on which CRUD operations actually worked end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import httpx
import websockets


_GW = "http://127.0.0.1:8766"
_WS = "ws://127.0.0.1:8766"


PROMPTS: list[tuple[str, str]] = [
    ("research_save",
     "Hey, can you research the Drake Cutlass Black ship from Star Citizen "
     "and save what you find? I want to be able to ask you about it later."),

    ("recall",
     "Cool. What did you find out about the Drake Cutlass Black?"),

    ("update",
     "Hmm, one correction — the Drake Cutlass Black has a crew of 2, not "
     "whatever bigger number you might have saved. Save that correction "
     "to your notes."),

    ("recall_after_update",
     "Now what's the current crew count for the Drake Cutlass Black "
     "according to your notes?"),

    ("remove",
     "Actually, forget all of that — delete your Drake Cutlass Black notes "
     "from the vault."),

    ("recall_after_remove",
     "Do you still have anything saved about the Drake Cutlass Black?"),
]


async def _pair() -> str:
    async with httpx.AsyncClient(base_url=_GW, timeout=30) as h:
        c = (await h.get("/v1/pair/new")).json()["code"]
        r = await h.post("/v1/pair", json={
            "code": c, "name": "vault-crud", "platform": "test",
        })
        return r.json()["token"]


async def _drive(token: str, prompt: str, label: str, deadline: float = 240.0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "label": label, "prompt": prompt,
        "delegations": [], "helpers": [],
        "actions_ok": [], "actions_fail": [],
        "reply": "", "wall_s": 0.0,
    }
    t0 = time.time()
    url = f"{_WS}/v1/chat/terry?token={token}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "user", "text": prompt}))
        end = t0 + deadline
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=end - time.time())
            except asyncio.TimeoutError:
                break
            ev = json.loads(raw)
            t = ev.get("type")
            if t == "thought":
                out["delegations"] = [d.get("role") for d in ev.get("delegations", [])]
            elif t == "helper_reply":
                out["helpers"].append({
                    "role": ev.get("role"),
                    "error": ev.get("error"),
                    "confidence": ev.get("confidence"),
                    "summary": (ev.get("output_summary") or "")[:120],
                })
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


def _print_turn(t: dict) -> None:
    print(f"\n[{t['label']}] you: {t['prompt']}", flush=True)
    print(f"  delegated: {t['delegations']}", flush=True)
    if t["helpers"]:
        for h in t["helpers"]:
            err = f" ERROR={h['error']}" if h.get("error") else ""
            print(f"   - {h['role']}: {h['summary']}{err}", flush=True)
    if t["actions_ok"]:
        print(f"  actions ok: {t['actions_ok']}", flush=True)
    if t["actions_fail"]:
        print(f"  actions fail:", flush=True)
        for a in t["actions_fail"]:
            print(f"    - {a}", flush=True)
    print(f"  Terry: {t['reply'][:300]}", flush=True)
    print(f"  wall:  {t['wall_s']}s", flush=True)


async def main() -> int:
    print(">> Pairing...", flush=True)
    token = await _pair()
    print(f">> Token: {token[:8]}...", flush=True)

    turns: list[dict] = []
    for label, prompt in PROMPTS:
        try:
            t = await _drive(token, prompt, label)
        except Exception as e:
            print(f"\n[{label}] CRASH: {e}", flush=True)
            t = {"label": label, "prompt": prompt, "reply": "",
                 "delegations": [], "helpers": [], "actions_ok": [],
                 "actions_fail": [], "wall_s": 0.0, "crashed": str(e)}
        turns.append(t)
        _print_turn(t)
        await asyncio.sleep(1)

    # ---------------------------------------------------------------- verdict
    print("\n" + "=" * 60, flush=True)
    print("VERDICT", flush=True)
    print("=" * 60, flush=True)

    by = {t["label"]: t for t in turns}

    research_wrote = "vault_learn" in (by.get("research_save", {}).get("actions_ok") or [])
    recall_used_lib = "librarian" in (by.get("recall", {}).get("delegations") or [])
    recall_has_content = (
        len(by.get("recall", {}).get("reply", "")) > 80
        and "drake" in by.get("recall", {}).get("reply", "").lower()
    )
    update_wrote = "vault_learn" in (by.get("update", {}).get("actions_ok") or [])
    update_recall_mentions_2 = "2" in by.get("recall_after_update", {}).get("reply", "")
    remove_attempted = (
        any(v in (by.get("remove", {}).get("actions_ok") or [])
            for v in ("vault_remove", "vault_delete", "vault_forget"))
        or "delete" in by.get("remove", {}).get("reply", "").lower()
        or "forget" in by.get("remove", {}).get("reply", "").lower()
    )
    remove_recall_empty = (
        "no" in by.get("recall_after_remove", {}).get("reply", "").lower()
        or "don't" in by.get("recall_after_remove", {}).get("reply", "").lower()
        or "removed" in by.get("recall_after_remove", {}).get("reply", "").lower()
    )

    rows = [
        ("CREATE — research wrote vault_learn", research_wrote),
        ("READ — recall delegated to librarian", recall_used_lib),
        ("READ — recall reply has Drake content", recall_has_content),
        ("UPDATE — correction wrote vault_learn", update_wrote),
        ("UPDATE — recall now mentions crew=2",  update_recall_mentions_2),
        ("DELETE — remove acknowledged (verb or text)", remove_attempted),
        ("DELETE — recall after remove says 'gone'",   remove_recall_empty),
    ]
    for label, ok in rows:
        # ASCII-only so Windows cp1252 console doesn't UnicodeError.
        print(f"  [{' OK ' if ok else 'FAIL'}] {label}", flush=True)

    return 0 if all(v for _, v in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
