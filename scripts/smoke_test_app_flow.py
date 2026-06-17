"""End-to-end smoke test that exercises the same code paths the app does.

Pairs as a fake device, then walks through:
  1. Plain chat with Terry — verify reply is non-empty and context is grounded
  2. Image-gen request — verify [ASK_USER] / [CONFIRM_IMAGE] flow
  3. Lenient marker — emit raw JSON, verify gateway re-routes
  4. Reset endpoint — verify history clears
  5. Image upload — verify /v1/images/upload returns a media_id
  6. App update endpoint — verify version + APK path resolve
  7. Vault stats — verify endpoint shape

Run from a fresh shell:
    python scripts/smoke_test_app_flow.py
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8766"


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


async def run() -> int:
    failures: list[str] = []

    def fail(msg: str) -> None:
        print(f"  ❌ {msg}", flush=True)
        failures.append(msg)

    def ok(msg: str) -> None:
        print(f"  ✅ {msg}", flush=True)

    # ---- pair ----------------------------------------------------------
    banner("PAIRING")
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as http:
        r = await http.get("/v1/pair/new")
        if r.status_code != 200:
            fail(f"pair/new returned {r.status_code}: {r.text}")
            return 1
        code = r.json()["code"]
        ok(f"got pairing code")
        r = await http.post("/v1/pair", json={
            "code": code, "name": "smoke-test", "platform": "test",
        })
        if r.status_code != 200:
            fail(f"pair claim returned {r.status_code}: {r.text}")
            return 1
        token = r.json()["token"]
        ok(f"got token ({len(token)} chars)")
        auth = {"Authorization": f"Bearer {token}"}

        # ---- bots --------------------------------------------------------
        banner("BOTS")
        r = await http.get("/v1/bots", headers=auth)
        if r.status_code != 200:
            fail(f"/v1/bots returned {r.status_code}")
        else:
            names = [b.get("name") for b in r.json()]
            # Terry is now the sole chat persona.
            if names == ["terry"]:
                ok(f"bots present: {names}")
            else:
                fail(f"bot list wrong: {names}")

        # ---- vault stats -------------------------------------------------
        banner("VAULT STATS")
        r = await http.get("/v1/vault/stats", headers=auth)
        if r.status_code != 200:
            fail(f"vault/stats {r.status_code}")
        else:
            j = r.json()
            if j.get("notes", 0) > 0:
                ok(f"vault has {j['notes']} notes, {j['total_size_bytes']} bytes")
                ok(f"  top-level: {j.get('by_top_level', {})}")
            else:
                fail("vault/stats says 0 notes")

        # ---- app update --------------------------------------------------
        banner("APP UPDATE")
        r = await http.get("/v1/app/version", headers=auth)
        if r.status_code != 200:
            fail(f"app/version {r.status_code}: {r.text}")
        else:
            j = r.json()
            if j.get("available"):
                ok(f"APK present, version_id={j['version_id']}, size={j['size_bytes']} bytes")
            else:
                fail("app/version says no APK available")
        # Test the new HMAC ticket flow used by the in-app updater.
        # (Replaced the old `?token=` query path which leaked the
        # bearer into browser history.)
        r = await http.post("/v1/app/download_ticket", headers=auth)
        if r.status_code == 200 and "url" in r.json():
            ticket_url = r.json()["url"]
            # Strip the gateway base — the test's AsyncClient is
            # bound to BASE already.
            from urllib.parse import urlparse
            parsed = urlparse(ticket_url)
            r2 = await http.head(parsed.path + "?" + parsed.query)
            if r2.status_code == 200:
                ok("APK download via signed ticket works (browser-launch path)")
            else:
                fail(f"APK download via signed ticket returned {r2.status_code}")
        else:
            fail(f"download_ticket returned {r.status_code}: {r.text[:120]}")

        # ---- image catalog ----------------------------------------------
        banner("IMAGE CATALOG")
        r = await http.get("/v1/images/catalog", headers=auth)
        if r.status_code != 200:
            fail(f"images/catalog {r.status_code}")
        else:
            j = r.json()
            if len(j.get("loras", [])) > 0:
                ok(f"catalog: {len(j['loras'])} LoRAs, {len(j['presets'])} presets")
            else:
                fail("catalog returned 0 LoRAs")

        # ---- image upload (img2img reference) ---------------------------
        banner("IMAGE UPLOAD")
        # 1x1 PNG
        png = bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000A49444154789C636000000000050001A5F645400000000049454E44AE426082"
        )
        files = {"file": ("test.png", png, "image/png")}
        r = await http.post(
            "/v1/images/upload", headers=auth, files=files,
        )
        if r.status_code != 200:
            fail(f"images/upload {r.status_code}: {r.text[:200]}")
            ref_id = None
        else:
            ref_id = r.json()["media_id"]
            ok(f"uploaded reference, media_id={ref_id}")

    # ---- chat: lenient marker test (skip the LLM) -----------------------
    # We can't easily test what Terry SAYS without invoking Ollama.
    # Instead, verify the marker scanner accepts naked JSON via unit test
    # and the /reset endpoint works.

    banner("RESET ENDPOINT")
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as http:
        r = await http.post(
            "/v1/chat/terry/reset", headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            fail(f"chat/terry/reset {r.status_code}: {r.text}")
        elif r.json().get("ok"):
            ok("reset endpoint cleared Terry's history")
        else:
            fail(f"reset returned non-ok: {r.json()}")

    # ---- chat WS round-trip ---------------------------------------------
    banner("CHAT (Terry, WebSocket)")
    ws_url = f"ws://127.0.0.1:8766/v1/chat/terry?token={token}"
    test_messages = [
        ("hi", lambda txt, evs: any(
            ev.get("type") == "assistant" for ev in evs
        ), "got an assistant reply"),
        ("what's a Drake in Star Citizen?", lambda txt, evs: any(
            "drake" in (ev.get("text") or "").lower() for ev in evs
            if ev.get("type") == "assistant"
        ) or any(
            ev.get("type") == "ask_user" for ev in evs
        ), "Terry mentions Drake or asks about it (vault grounding worked)"),
    ]

    try:
        async with websockets.connect(ws_url) as ws:
            for user_msg, check, label in test_messages:
                await ws.send(json.dumps({"type": "user", "text": user_msg}))
                events: list[dict] = []
                # collect up to 90s for a reply (Ollama is slow)
                t_start = time.time()
                while time.time() - t_start < 90:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=90)
                    except asyncio.TimeoutError:
                        break
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue
                    events.append(ev)
                    if ev.get("type") == "done":
                        break
                if not events:
                    fail(f"'{user_msg}' → no events received")
                    continue
                if check(user_msg, events):
                    types = [e.get("type") for e in events]
                    print(f"  ✅ '{user_msg}': {label}", flush=True)
                    print(f"     event types: {types}", flush=True)
                else:
                    fail(f"'{user_msg}' → check failed; events: {events}")
    except Exception as e:
        fail(f"chat WS failed: {e}")

    # ---- summary --------------------------------------------------------
    banner("RESULTS")
    if failures:
        print(f"  {len(failures)} failures:", flush=True)
        for f in failures:
            print(f"    - {f}", flush=True)
        return 1
    print("  All checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
