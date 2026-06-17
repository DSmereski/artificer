"""Phase-2 forget/edit/correction eval. Drives natural-language vault
mutations against the existing SC corpus and audits which notes
landed/disappeared.

Tests:
  E01 — delete by topic ("delete the Banu note") → faction-banu.md gone
  E02 — delete by quoted title ("forget 'Faction — Nine Tails'") → gone
  E03 — correct a fact ("Hurston actually has 5 moons: …") → body merge
  E04 — append to a note ("add to the Vanduul note that …") → body merge
  E05 — delete multiple ("delete the Lorville note") → gone
  E06 — re-save deleted ("Save 'Banu test' — alien race that trades.") → new file
  E07 — forget across audience ("forget the test Banu note") → gone

After each turn, capture vault filesystem state + actions fired.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import httpx
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import os as _os
VAULT_ROOT = Path(_os.environ.get("HIVE_VAULT_PATH", "./vault")) / "knowledge" / "2026" / "06"
BACKUP_ROOT = Path(_os.environ.get("HIVE_TRANSCRIPT_DIR", "./tmp/ai-team")) / "sc_backup"


# Each entry: (id, prompt, expected_verb, expected_file_state, file_filter).
# expected_file_state ∈ {"gone", "present", "modified", "new"}
# file_filter: substring of filename to check
TESTS: list[tuple[str, str, str, str, str]] = [
    ("E01_forget_banu",
     "Delete the note about the Banu faction.",
     "vault_forget", "gone", "faction-banu.md"),
    ("E02_forget_nine_tails",
     "Forget the 'Faction — Nine Tails' note.",
     "vault_forget", "gone", "faction-nine-tails.md"),
    ("E03_correct_hurston",
     "Update the Hurston note — actually it has 5 moons: Aberdeen, "
     "Arial, Magda, Ita, and Etna. Save this correction.",
     "vault_learn", "modified", "hurston.md"),
    ("E04_append_vanduul",
     "Add to the Vanduul note: their primary fighters are the Scythe "
     "and the Blade. Save it.",
     "vault_learn", "modified", "faction-vanduul.md"),
    ("E05_forget_lorville",
     "Forget the Lorville location note.",
     "vault_forget", "gone", "locations-lorville.md"),
    ("E06_resave_after_delete",
     "Save 'Banu refresh' — peaceful trading alien race who fly the "
     "Banu Defender, recently added back after a test.",
     "vault_learn", "new", "banu-refresh"),
    ("E07_forget_resave",
     "Now forget the Banu refresh note.",
     "vault_forget", "gone", "banu-refresh"),
]


async def pair(host: str, name: str) -> str:
    async with httpx.AsyncClient(base_url=host, timeout=10.0) as c:
        code = (await c.get("/v1/pair/new")).json()["code"]
        return (await c.post(
            "/v1/pair",
            json={"code": code, "name": name, "platform": "py-driver"},
        )).json()["token"]


def snapshot() -> set[str]:
    return {p.name for p in VAULT_ROOT.glob("*.md")}


def find_match(snap: set[str], frag: str) -> str | None:
    frag = frag.lower()
    for f in snap:
        if frag in f.lower():
            return f
    return None


def file_size(name: str | None) -> int | None:
    if not name:
        return None
    p = VAULT_ROOT / name
    return p.stat().st_size if p.exists() else None


async def drive(host: str, token: str, out: Path) -> None:
    ws_url = host.replace("http://", "ws://") + "/v1/chat/terry"
    transcript: list[dict] = []
    async with websockets.connect(
        ws_url, additional_headers={"Authorization": f"Bearer {token}"},
        max_size=2**22,
    ) as ws:
        for tid, prompt, verb, state, frag in TESTS:
            pre_snap = snapshot()
            pre_match = find_match(pre_snap, frag)
            pre_size = file_size(pre_match)
            print(f"\n=== {tid} ===\n> {prompt}")
            await ws.send(json.dumps({"type": "user", "text": prompt}))
            t0 = time.time()
            reply_parts: list[str] = []
            actions: list[str] = []
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
                        actions.append(v)
                elif t == "error":
                    print(f"  !! error: {ev.get('message','?')}")
                    break
            dt = time.time() - t0
            # Give vault_writer a moment to flush.
            await asyncio.sleep(2)
            post_snap = snapshot()
            post_match = find_match(post_snap, frag)
            post_size = file_size(post_match)
            print(f"  {dt:.1f}s actions={actions}")
            print(f"  pre={pre_match}(size={pre_size}) post={post_match}(size={post_size})")
            print(f"  reply[:200]: {''.join(reply_parts)[:200]}")
            transcript.append({
                "id": tid, "prompt": prompt,
                "expected_verb": verb, "expected_state": state, "file_frag": frag,
                "pre_file": pre_match, "pre_size": pre_size,
                "post_file": post_match, "post_size": post_size,
                "actions": actions, "reply": "".join(reply_parts),
                "elapsed_s": dt,
            })
    out.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"\ntranscript -> {out}")


def grade(transcript_path: Path) -> None:
    data = json.loads(transcript_path.read_text(encoding="utf-8"))
    total = len(data)
    passed = 0
    failed: list[tuple[str, str]] = []
    for t in data:
        tid = t["id"]
        expected_state = t["expected_state"]
        pre = t["pre_file"]
        post = t["post_file"]
        pre_size = t["pre_size"]
        post_size = t["post_size"]
        actions = t.get("actions", [])
        verb = t["expected_verb"]

        # Verdict.
        if expected_state == "gone":
            ok = pre is not None and post is None
        elif expected_state == "present":
            ok = post is not None
        elif expected_state == "modified":
            ok = pre is not None and post is not None and post_size != pre_size
        elif expected_state == "new":
            ok = pre is None and post is not None
        else:
            ok = False

        if ok:
            passed += 1
        else:
            failed.append((tid, (
                f"state={expected_state} pre={pre} post={post} "
                f"sizes={pre_size}->{post_size} actions={actions}"
            )))

    print(f"\n=== GRADE === passed {passed}/{total}")
    if failed:
        print("\nfailed:")
        for tid, why in failed:
            print(f"  {tid}: {why}")


def backup_vault() -> None:
    BACKUP_ROOT.parent.mkdir(parents=True, exist_ok=True)
    if BACKUP_ROOT.exists():
        shutil.rmtree(BACKUP_ROOT)
    shutil.copytree(VAULT_ROOT, BACKUP_ROOT)
    print(f"backup -> {BACKUP_ROOT}")


def restore_vault() -> None:
    if not BACKUP_ROOT.exists():
        print("no backup to restore")
        return
    # Restore everything: remove any new files, restore deleted ones.
    backup_files = {p.name for p in BACKUP_ROOT.glob("*.md")}
    current = {p.name for p in VAULT_ROOT.glob("*.md")}
    # Add back missing.
    for missing in backup_files - current:
        shutil.copy(BACKUP_ROOT / missing, VAULT_ROOT / missing)
    # Remove ones not in backup.
    for extra in current - backup_files:
        (VAULT_ROOT / extra).unlink()
    # Overwrite any whose sizes differ.
    for fname in backup_files & current:
        src = BACKUP_ROOT / fname
        dst = VAULT_ROOT / fname
        if src.stat().st_size != dst.stat().st_size:
            shutil.copy(src, dst)
    print(f"restored from {BACKUP_ROOT}")


async def _run() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--out", default="C:/tmp/ai-team/sc_edit.json")
    p.add_argument("--token", default=None)
    p.add_argument("--name", default="sc-edit")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument("--restore", action="store_true",
                   help="Restore vault from backup and exit.")
    p.add_argument("--grade-only", action="store_true")
    args = p.parse_args()
    out = Path(args.out)
    if args.restore:
        restore_vault()
        return
    if not args.grade_only:
        if not args.no_backup:
            backup_vault()
        token = args.token or await pair(args.host, args.name)
        await drive(args.host, token, out)
    grade(out)


if __name__ == "__main__":
    asyncio.run(_run())
