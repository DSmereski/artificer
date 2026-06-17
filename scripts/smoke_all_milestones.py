"""End-to-end smoke that exercises every M1-M6 surface live.

Run after the gateway is up. Prints a checklist; non-zero exit if
anything fails.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

# Make sibling gateway/ + services/ packages importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx

BASE = "http://127.0.0.1:8766"
SCOUT = "http://127.0.0.1:8767"


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


async def run() -> int:
    failures: list[str] = []

    def fail(msg: str) -> None:
        print(f"  FAIL: {msg}", flush=True)
        failures.append(msg)

    def ok(msg: str) -> None:
        print(f"  OK  : {msg}", flush=True)

    async with httpx.AsyncClient(base_url=BASE, timeout=300.0) as h:
        # ----- pair --------------------------------------------------
        banner("PAIR (M1 baseline)")
        r = await h.get("/v1/pair/new")
        if r.status_code != 200:
            fail(f"pair/new {r.status_code}")
            return 1
        code = r.json()["code"]
        r = await h.post("/v1/pair", json={
            "code": code, "name": "smoke-all", "platform": "test",
        })
        if r.status_code != 200:
            fail(f"pair {r.status_code}")
            return 1
        token = r.json()["token"]
        H = {"Authorization": f"Bearer {token}"}
        ok(f"paired (token len {len(token)})")

        # ----- M1: bot list ----------------------------------------
        banner("M1: /v1/bots returns Terry only")
        r = await h.get("/v1/bots", headers=H)
        names = [b["name"] for b in r.json()]
        if names == ["terry"]:
            ok(f"bot list: {names}")
        else:
            fail(f"bot list wrong: {names}")

        # ----- M1: legacy redirect WS -------------------------------
        banner("M1: legacy /v1/chat/maggy soft-redirects to terry")
        # This requires WS; use websockets package.
        import websockets
        try:
            async with websockets.connect(
                f"ws://127.0.0.1:8766/v1/chat/maggy?token={token}",
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                ev = json.loads(raw)
                if ev.get("type") == "system_notice" and "terry" in ev.get("text", "").lower():
                    ok(f"redirect notice: {ev['text']}")
                else:
                    fail(f"unexpected first event: {ev}")
        except Exception as e:
            fail(f"legacy WS failed: {e}")

        # ----- M1: scout-daemon RPC ---------------------------------
        banner("M1: scout-daemon RPC (127.0.0.1:8767)")
        try:
            with urllib.request.urlopen(
                f"{SCOUT}/sysmon/health", timeout=2,
            ) as resp:
                j = json.loads(resp.read())
                if j.get("ok"):
                    ok("scout-daemon /sysmon/health OK")
                else:
                    fail(f"sysmon health: {j}")
        except Exception as e:
            print(f"  WARN: scout-daemon not running: {e}", flush=True)

        # ----- M2: /v1/models ---------------------------------------
        banner("M2: /v1/models (catalog)")
        r = await h.get("/v1/models", headers=H)
        d = r.json()
        # Catalog now has planner-qwen + nomic-embed (every helper shares
        # planner-qwen). Helpers stay at 10 distinct roles.
        if len(d["models"]) >= 2 and len(d["helpers"]) == 10:
            ok(f"{len(d['models'])} models, {len(d['helpers'])} helpers")
        else:
            fail(f"unexpected catalog shape: "
                 f"{len(d['models'])} models, {len(d['helpers'])} helpers")
        for m in d["models"]:
            if not m.get("available", True):
                print(f"      WARN: model {m['id']} unavailable", flush=True)

        # ----- M2: /v1/hive/info ------------------------------------
        banner("M2: /v1/hive/info")
        r = await h.get("/v1/hive/info", headers=H)
        info = r.json()
        if (
            len(info["helper_roles"]) == 10
            and info["budget"]["max_concurrent_helpers"] == 5
            and info["budget"]["vram_budget_mb"] == 14000
        ):
            ok(f"helpers: {info['helper_roles']}")
            ok(f"budget: {info['budget']}")
        else:
            fail(f"hive/info wrong shape: {info}")

        # ----- M2: /v1/hive/test live -------------------------------
        banner("M2: /v1/hive/test (LIVE — qwen3:8b planner)")
        t0 = time.time()
        r = await h.post(
            "/v1/hive/test", headers=H,
            json={"user_msg": "hi terry", "device_id": "smoke-all"},
        )
        if r.status_code != 200:
            fail(f"hive/test {r.status_code}: {r.text[:200]}")
        else:
            d = r.json()
            elapsed = time.time() - t0
            if d.get("error"):
                fail(f"hive/test error: {d['error']}")
            elif "planner" in d["helpers_used"]:
                ok(f"reply: {d['reply'][:80]}")
                ok(f"helpers: {d['helpers_used']}, "
                   f"tokens={d['total_tokens']}, "
                   f"latency={d['total_latency_ms']}ms (wall {elapsed:.1f}s)")
                # Always-think-first: thought event must precede assistant.
                types = [e["type"] for e in d["events"]]
                if types[0] == "thought":
                    ok("thought event came first (always-think-first)")
                else:
                    fail(f"first event was {types[0]}, expected 'thought'")
            else:
                fail(f"planner not in helpers_used: {d}")

        # ----- M3: /v1/skills ---------------------------------------
        banner("M3: /v1/skills")
        r = await h.get("/v1/skills", headers=H)
        skills = r.json()["skills"]
        names = {s["name"] for s in skills}
        if "research-and-cite" in names:
            ok(f"{len(skills)} skill(s) loaded: {sorted(names)}")
        else:
            fail(f"missing research-and-cite: {sorted(names)}")

        banner("M3: /v1/skills/research-and-cite (full body)")
        r = await h.get("/v1/skills/research-and-cite", headers=H)
        s = r.json()
        if (
            s["read_only"] is True
            and "research X" in s["triggers"]
            and "Research and cite" in s["body"]
        ):
            ok(f"skill body length {len(s['body'])}, "
               f"{len(s['constraints'])} constraints")
        else:
            fail(f"skill detail malformed: {s}")

        # ----- M3: Claude Code bridge -------------------------------
        banner("M3: Claude Code junction")
        from pathlib import Path
        team_dir = Path.home() / ".claude" / "skills" / "team"
        if team_dir.is_dir():
            files = sorted(p.name for p in team_dir.glob("*.md"))
            if "research-and-cite.md" in files:
                ok(f"junction visible to Claude Code: {files}")
            else:
                fail(f"junction missing skills: {files}")
        else:
            fail(f"junction dir not found: {team_dir}")

        # ----- M4.1: SSRF guard -------------------------------------
        banner("M4.1: SSRF guard (validate_url)")
        from gateway.safe_fetcher import validate_url
        cases = [
            ("http://localhost/", True),
            ("http://10.0.0.1/", True),
            ("file:///etc/passwd", True),
            ("javascript:alert(1)", True),
            ("https://example.com/", False),
        ]
        all_ok = True
        for url, should_block in cases:
            blocked = validate_url(url) is not None
            if blocked != should_block:
                fail(f"validate_url({url}): blocked={blocked}, expected={should_block}")
                all_ok = False
        if all_ok:
            ok(f"SSRF guard rejects all 4 dangerous URLs, accepts public")

        # ----- M4.2: REMEMBER with sources --------------------------
        banner("M4.2: parse_remember with sources")
        from gateway.conversation_markers import parse_remember
        out = parse_remember(json.dumps({
            "category": "knowledge", "title": "x", "body": "b",
            "sources": [
                {"url": "https://a.example.com", "title": "A"},
                {"url": "https://b.example.com"},
            ],
            "corroboration": 2,
        }))
        if out and out.get("extra", {}).get("corroboration") == 2:
            ok(f"sources: {len(out['extra']['sources'])}, "
               f"corroboration={out['extra']['corroboration']}")
        else:
            fail(f"parse_remember sources broken: {out}")

        # ----- M5.1: ImageBuildState round-trip ---------------------
        banner("M5.1: ImageBuildState persistence")
        from gateway.image_build_state import ImageBuildStore
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as td:
            store = ImageBuildStore(Path(td))
            store.update("dev1", {"subject": "elf", "aspect": "portrait"})
            store2 = ImageBuildStore(Path(td))     # simulated restart
            s2 = store2.get("dev1")
            if s2 and s2.subject == "elf" and s2.is_ready():
                ok("survived restart, slots filled, is_ready() true")
            else:
                fail(f"persistence failed: {s2}")

        # ----- M5.2: ConversationMemory -----------------------------
        banner("M5.2: ConversationMemory tiered")
        from gateway.conversation_memory import MemoryStore
        with tempfile.TemporaryDirectory() as td:
            mstore = MemoryStore(Path(td), bot="terry")
            mstore.apply_summary(
                user_id=1,
                summary="user is debugging the hive",
                open_tasks=["finish M6"],
                decisions=["use qwen3:8b"],
                user_facts=["user is named Penguin"],
            )
            mstore2 = MemoryStore(Path(td), bot="terry")
            mem = mstore2.get(1)
            block = mem.render_for_planner()
            if "Penguin" in block and "finish M6" in block:
                ok("summary survives reload, render_for_planner OK")
            else:
                fail(f"memory block missing fields: {block}")

        # ----- M6.3: /v1/telemetry/last_turn ------------------------
        banner("M6.3: /v1/telemetry/last_turn")
        r = await h.get("/v1/telemetry/last_turn", headers=H)
        recs = r.json()["records"]
        if any(r["bot"] == "terry" for r in recs):
            ok(f"{len(recs)} turn(s) recorded")
        else:
            fail(f"no telemetry recorded: {recs}")

        # ----- M6.2: Sysmon helper (live, hits scout-daemon) -------
        banner("M6.2: Sysmon helper end-to-end")
        from gateway.sysmon_client import fetch_snapshot
        snap = await fetch_snapshot()
        if snap is None:
            print("  WARN: scout-daemon not running, skipping sysmon helper", flush=True)
        else:
            ok(f"snapshot fetched: GPUs={list(snap.get('gpu_temps') or {})}, "
               f"disks={list(snap.get('disk_free_gb') or {})}")

    # ----- summary ----------------------------------------------------
    banner("RESULTS")
    if failures:
        print(f"  {len(failures)} failures:", flush=True)
        for f in failures:
            print(f"    - {f}", flush=True)
        return 1
    print("  All M1-M6 surfaces verified.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
