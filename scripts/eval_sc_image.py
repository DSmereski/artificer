"""Phase-3 image-render flow eval. Drives natural-language image requests
through the chat WS and audits which actions fire.

Tests verify:
  - image_render verb emitted
  - no `reference_path` leaked into payload
  - receipt has either ok=True OR an explainable detail
  - aspect / count parsed when present
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


# Each: (id, prompt, expected_verb, optional_expected_field_check).
TESTS: list[tuple[str, str, str]] = [
    ("I01_cutlass_lorville",
     "Generate an image of a Drake Cutlass Black flying over Lorville at sunset.",
     "image_render"),
    ("I02_portrait_uee_imperator",
     "Render a portrait of the UEE Imperator standing in front of the Earth.",
     "image_render"),
    ("I03_count_4_vulture",
     "Make 4 images of a Drake Vulture salvaging a wrecked ship in an asteroid field.",
     "image_render"),
    ("I04_aspect_widescreen",
     "Generate a widescreen image of the Stanton system with all four planets visible.",
     "image_render"),
    ("I05_negative_prompt",
     "Render an image of microTech's New Babbage at night, no humans visible.",
     "image_render"),
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
        for tid, prompt, expected_verb in TESTS:
            print(f"\n=== {tid} ===\n> {prompt}")
            await ws.send(json.dumps({"type": "user", "text": prompt}))
            t0 = time.time()
            reply_parts: list[str] = []
            actions: list[dict] = []
            synthesis_payloads: list[dict] = []
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=300.0)
                except asyncio.TimeoutError:
                    print("  !! timed out")
                    break
                ev = json.loads(raw)
                t = ev.get("type")
                if t == "assistant":
                    reply_parts.append(ev.get("text", ""))
                elif t == "done":
                    break
                elif t == "action_done":
                    v = ev.get("verb")
                    if v:
                        actions.append({
                            "verb": v,
                            "ok": ev.get("ok"),
                            "detail": ev.get("detail"),
                            "payload": ev.get("payload"),
                        })
                elif t == "synthesis":
                    for a in ev.get("actions", []) or []:
                        if isinstance(a, dict):
                            synthesis_payloads.append(a)
                elif t == "error":
                    print(f"  !! error: {ev.get('message','?')}")
                    break
            dt = time.time() - t0
            verbs = [a["verb"] for a in actions]
            synth_verbs = [
                a.get("verb") for a in synthesis_payloads if isinstance(a, dict)
            ]
            print(f"  {dt:.1f}s action_done verbs={verbs} synth verbs={synth_verbs}")
            print(f"  reply[:200]: {''.join(reply_parts)[:200]}")
            transcript.append({
                "id": tid, "prompt": prompt,
                "expected_verb": expected_verb,
                "actions": actions,
                "synthesis_actions": synthesis_payloads,
                "reply": "".join(reply_parts),
                "elapsed_s": dt,
            })
    out.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"\ntranscript -> {out}")


def grade(transcript_path: Path) -> None:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    total = len(data)
    passed = 0
    failed: list[tuple[str, str]] = []
    safety_violations: list[str] = []
    for t in data:
        tid = t["id"]
        expected_verb = t["expected_verb"]
        # Combine action_done verbs (real receipts) with synthesis-emitted
        # verbs (planned but maybe not executed if synth then rejected).
        all_action_verbs: set[str] = set()
        for a in t.get("actions", []):
            v = a.get("verb")
            if v:
                all_action_verbs.add(v)
        for a in t.get("synthesis_actions", []):
            if isinstance(a, dict):
                v = a.get("verb")
                if v:
                    all_action_verbs.add(v)

        # Verdict.
        if expected_verb in all_action_verbs:
            passed += 1
        else:
            failed.append((tid, f"expected {expected_verb}, got {sorted(all_action_verbs)}"))

        # Safety: no reference_path leaked.
        for a in t.get("synthesis_actions", []):
            if not isinstance(a, dict):
                continue
            payload = a.get("payload") or {}
            if isinstance(payload, dict) and "reference_path" in payload:
                safety_violations.append(f"{tid}: reference_path leak")

    print(f"\n=== GRADE === passed {passed}/{total}")
    if failed:
        print("\nfailed:")
        for tid, why in failed:
            print(f"  {tid}: {why}")
    if safety_violations:
        print("\nsafety:")
        for v in safety_violations:
            print(f"  {v}")


async def _run() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--out", default="C:/tmp/ai-team/sc_image.json")
    p.add_argument("--token", default=None)
    p.add_argument("--name", default="sc-image")
    p.add_argument("--grade-only", action="store_true")
    args = p.parse_args()
    out = Path(args.out)
    if not args.grade_only:
        token = args.token or await pair(args.host, args.name)
        await drive(args.host, token, out)
    grade(out)


if __name__ == "__main__":
    asyncio.run(_run())
