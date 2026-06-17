"""End-to-end matrix: hit every major REST endpoint + 3 representative
chat turns over one paired session. Produces a pass/fail row per
feature. ASCII-safe output for Windows console.
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


def _safe(s: str) -> str:
    try:
        s.encode("cp1252")
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode("ascii", "replace").decode()


async def _pair() -> str:
    async with httpx.AsyncClient(base_url=_GW, timeout=30) as h:
        c = (await h.get("/v1/pair/new")).json()["code"]
        r = await h.post("/v1/pair", json={
            "code": c, "name": "matrix-smoke", "platform": "test",
        })
        return r.json()["token"]


async def _drive_chat(token: str, prompt: str, deadline: float = 180.0) -> dict[str, Any]:
    out = {
        "delegations": [], "helper_errors": [],
        "actions_ok": [], "reply": "", "wall_s": 0.0,
    }
    t0 = time.time()
    url = f"{_WS}/v1/chat/terry?token={token}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "user", "text": prompt}))
        while time.time() - t0 < deadline:
            try:
                ev = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=deadline - (time.time() - t0)))
            except asyncio.TimeoutError:
                break
            t = ev.get("type")
            if t == "thought":
                out["delegations"] = [d.get("role") for d in ev.get("delegations", [])]
            elif t == "helper_reply" and ev.get("error"):
                out["helper_errors"].append(f"{ev.get('role')}: {ev['error'][:60]}")
            elif t == "synthesis":
                for a in ev.get("actions") or []:
                    if isinstance(a, dict) and a.get("ok") is True:
                        out["actions_ok"].append(a.get("verb", "?"))
            elif t == "assistant":
                out["reply"] = ev.get("text", "")
            elif t == "done":
                break
    out["wall_s"] = round(time.time() - t0, 1)
    return out


async def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        rows.append((name, ok, detail[:120]))

    print(">> Pairing test session...", flush=True)
    try:
        token = await _pair()
        add("REST POST /v1/pair/new+/v1/pair", True, f"token={token[:8]}")
    except Exception as e:
        add("REST POST /v1/pair/new+/v1/pair", False, _safe(str(e)))
        return _print_verdict(rows)

    headers = {"Authorization": f"Bearer {token}"}

    # ---------------------------------------------------------------- REST surface
    async with httpx.AsyncClient(base_url=_GW, timeout=30) as h:
        for name, path, key in [
            ("REST GET /v1/bots",            "/v1/bots",          None),
            ("REST GET /v1/config/ntfy",     "/v1/config/ntfy",   "topics"),
            ("REST GET /v1/skills",          "/v1/skills",        "skills"),
            ("REST GET /v1/recipes",         "/v1/recipes",       "recipes"),
            ("REST GET /v1/loras",           "/v1/loras",         "loras"),
            ("REST GET /v1/calendar/jobs",   "/v1/calendar/jobs", "jobs"),
            ("REST GET /v1/scout/status",    "/v1/scout/status",  None),
            ("REST GET /v1/images/catalog",  "/v1/images/catalog","loras"),
            ("REST GET /v1/telemetry/last_turn",  "/v1/telemetry/last_turn",  None),
            ("REST GET /v1/models",          "/v1/models",        None),
            ("REST GET /v1/vault/tree",      "/v1/vault/tree",    None),
        ]:
            try:
                r = await h.get(path, headers=headers, timeout=15)
                ok = r.status_code == 200
                detail = f"{r.status_code}"
                if ok and key:
                    body = r.json()
                    detail = f"200 {key}={len(body[key]) if isinstance(body, dict) and key in body else '?'}"
                elif ok:
                    detail = "200"
                add(name, ok, detail)
            except Exception as e:
                add(name, False, _safe(str(e)))

        # Vault search needs an actual query.
        try:
            r = await h.get("/v1/vault/search", headers=headers,
                            params={"q": "drake cutlass", "k": 3}, timeout=15)
            add("REST GET /v1/vault/search?q=drake",
                r.status_code == 200,
                f"{r.status_code} hits={len(r.json()) if r.status_code == 200 else '?'}")
        except Exception as e:
            add("REST GET /v1/vault/search?q=drake", False, _safe(str(e)))

        # Calendar create + read + delete
        try:
            from datetime import datetime, timezone, timedelta
            fire = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            r = await h.post("/v1/calendar/jobs", headers=headers, json={
                "title": "matrix-smoke",
                "scheduled_at": fire,
                "recurrence": "none",
                "action_verb": "ntfy_push",
                "action_payload": {"title": "x", "message": "y"},
                "notify": False,
            })
            ok = r.status_code == 200
            jid = r.json().get("id") if ok else None
            add("REST POST /v1/calendar/jobs", ok, f"{r.status_code} id={jid}")
            if jid:
                r = await h.get(f"/v1/calendar/jobs/{jid}", headers=headers)
                add("REST GET  /v1/calendar/jobs/{id}",
                    r.status_code == 200, f"{r.status_code}")
                r = await h.delete(f"/v1/calendar/jobs/{jid}", headers=headers)
                add("REST DELETE /v1/calendar/jobs/{id}",
                    r.status_code in (200, 204), f"{r.status_code}")
        except Exception as e:
            add("REST calendar CRUD", False, _safe(str(e)))

        # LoRA import by URL — should accept the request and start a job.
        try:
            r = await h.post("/v1/loras/import", headers=headers,
                             json={"url": "https://civitai.com/models/241797/sample-model"})
            ok = r.status_code in (200, 201)
            jid = r.json().get("id") if ok else None
            add("REST POST /v1/loras/import (start job)",
                ok, f"{r.status_code} job={jid}")
        except Exception as e:
            add("REST POST /v1/loras/import", False, _safe(str(e)))

        # Devices listing.
        try:
            r = await h.get("/v1/devices", headers=headers)
            add("REST GET /v1/devices", r.status_code == 200, f"{r.status_code}")
        except Exception as e:
            add("REST GET /v1/devices", False, _safe(str(e)))

    # ---------------------------------------------------------------- chat surface
    print("\n>> Chat: greeting (direct_reply path)...", flush=True)
    try:
        out = await _drive_chat(token, "hey", deadline=120)
        ok = bool(out["reply"]) and len(out["reply"]) > 5
        add("CHAT greeting (direct_reply)",
            ok, f"reply_len={len(out['reply'])} {out['wall_s']}s")
    except Exception as e:
        add("CHAT greeting", False, _safe(str(e)))

    print(">> Chat: sysmon (helper dispatch)...", flush=True)
    try:
        out = await _drive_chat(token, "what's the gpu temp?", deadline=180)
        ok = "sysmon" in out["delegations"] and not out["helper_errors"]
        has_temp = any(s in out["reply"].lower() for s in ["c", "temp", "degree"])
        add("CHAT sysmon (helper + reply)", ok and has_temp,
            f"deleg={out['delegations']} {out['wall_s']}s")
    except Exception as e:
        add("CHAT sysmon", False, _safe(str(e)))

    print(">> Chat: librarian recall...", flush=True)
    try:
        out = await _drive_chat(token,
            "tell me about the Drake Cutlass — use what's in your notes",
            deadline=180)
        ok = "librarian" in out["delegations"] and bool(out["reply"])
        add("CHAT librarian (recall path)", ok,
            f"deleg={out['delegations']} reply_len={len(out['reply'])} {out['wall_s']}s")
    except Exception as e:
        add("CHAT librarian", False, _safe(str(e)))

    return _print_verdict(rows)


def _print_verdict(rows: list[tuple[str, bool, str]]) -> int:
    width = max(len(r[0]) for r in rows) + 2
    print("\n" + "=" * 70, flush=True)
    print("FEATURE MATRIX", flush=True)
    print("=" * 70, flush=True)
    passed = sum(1 for _, ok, _ in rows if ok)
    for name, ok, detail in rows:
        marker = "[ OK ]" if ok else "[FAIL]"
        print(f"  {marker} {name.ljust(width)}{_safe(detail)}", flush=True)
    print(f"\n  TOTAL: {passed}/{len(rows)} passed", flush=True)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
