"""Run one or all long-conversation E2E scenarios from scripts/scenarios/.

Each scenario is a JSONL file with `{"role": "user"|"sleep"|"meta", ...}`
lines. The runner pairs to the gateway as a fake device, opens a chat
WS for whichever bot the meta line names (default ``terry``), feeds
each user turn, and captures every WS frame the gateway emits.

Output per run:
  runs/<scenario>/<UTC-iso>/transcript.json   — alternating user/assistant
  runs/<scenario>/<UTC-iso>/events.jsonl      — every raw frame
  runs/<scenario>/<UTC-iso>/meta.json         — scenario metadata + timing

Usage:
    python scripts/run_scenarios.py --scenario 01 --token <t>
    python scripts/run_scenarios.py --all --token <t> --out runs/
    python scripts/run_scenarios.py --list
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import sys
import time
from pathlib import Path
from typing import Any

# Force UTF-8 stdout/stderr so scenario headlines (which include unicode like
# arrows and em-dashes) render on Windows consoles that default to cp1252.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _scenario_paths() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("[0-9][0-9]_*.jsonl"))


def _resolve_scenario(name: str) -> Path:
    """Accept '01', '01_tokyo-trip-planning', or full filename."""
    name = name.strip()
    direct = SCENARIOS_DIR / name
    if direct.exists():
        return direct
    if not name.endswith(".jsonl"):
        as_jsonl = SCENARIOS_DIR / f"{name}.jsonl"
        if as_jsonl.exists():
            return as_jsonl
    matches = [p for p in _scenario_paths() if p.name.startswith(f"{name}_")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(
            f"ambiguous scenario name {name!r}: {[p.name for p in matches]}"
        )
    raise SystemExit(f"no scenario matches {name!r} in {SCENARIOS_DIR}")


def _load_scenario(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (meta, turns). meta is the first ``role: meta`` line or {}."""
    meta: dict[str, Any] = {}
    turns: list[dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{i}: bad JSON: {e}") from e
        role = obj.get("role")
        if role == "meta":
            meta = obj
            continue
        if role in ("user", "sleep"):
            turns.append(obj)
            continue
        # ignore unknown roles so the schema can extend cleanly
    return meta, turns


async def _run_one(
    *,
    scenario_path: Path,
    host: str,
    token: str,
    out_root: Path,
    per_turn_timeout_s: float = 240.0,
) -> Path:
    """Run a single scenario end-to-end. Returns the run directory."""
    # Lazy-import so --list doesn't require websockets/httpx installed.
    import websockets  # type: ignore[import-not-found]

    meta, turns = _load_scenario(scenario_path)
    bot = (meta.get("bot") or "terry").strip() or "terry"
    scenario_slug = scenario_path.stem
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / scenario_slug / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    ws_host = host.replace("http://", "ws://").replace("https://", "wss://")
    # Isolate every scenario run in its own thread_id so MemoryStore +
    # chat_log don't bleed owner state into the conversation. Without
    # this, all scenarios share the single-owner default thread and
    # inherit unrelated history (cancer-research turn 2 hallucinating
    # Star Citizen content was an actual symptom).
    thread_id = f"scn-{scenario_slug}-{stamp}"
    url = f"{ws_host}/v1/chat/{bot}?thread_id={thread_id}"
    headers = {"Authorization": f"Bearer {token}"}

    transcript: list[dict[str, Any]] = []
    events_path = run_dir / "events.jsonl"
    transcript_path = run_dir / "transcript.json"
    meta_path = run_dir / "meta.json"

    started_at = time.time()
    print(f"\n>>> {scenario_slug} (bot={bot}, turns={len(turns)}) -> {run_dir}")

    with events_path.open("w", encoding="utf-8") as events_f:
        async with websockets.connect(
            url, additional_headers=headers, max_size=2**22,
        ) as ws:
            for turn_idx, turn in enumerate(turns, 1):
                role = turn.get("role")
                if role == "sleep":
                    secs = float(turn.get("seconds", 0) or 0)
                    if secs > 0:
                        print(f"  [sleep {secs:.0f}s]")
                        await asyncio.sleep(secs)
                    continue
                if role != "user":
                    continue
                user_text = str(turn.get("text", ""))
                print(f"\n=== TURN {turn_idx}: USER ===")
                print(user_text)
                await ws.send(json.dumps({"type": "user", "text": user_text}))
                transcript.append({
                    "turn": turn_idx, "role": "user", "text": user_text,
                    "ts": time.time(),
                })

                assistant_chunks: list[str] = []
                turn_events: list[dict[str, Any]] = []
                t0 = time.time()
                while True:
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=per_turn_timeout_s,
                        )
                    except asyncio.TimeoutError:
                        elapsed = time.time() - t0
                        print(f"  ! TIMEOUT after {elapsed:.1f}s")
                        turn_events.append({"type": "_timeout", "after_s": elapsed})
                        events_f.write(
                            json.dumps({"turn": turn_idx, "type": "_timeout",
                                        "after_s": elapsed}) + "\n"
                        )
                        events_f.flush()
                        break
                    msg = json.loads(raw)
                    turn_events.append(msg)
                    events_f.write(json.dumps({"turn": turn_idx, **msg}) + "\n")
                    events_f.flush()
                    t = msg.get("type")
                    if t == "assistant":
                        assistant_chunks.append(msg.get("text", ""))
                    elif t == "done":
                        break
                    elif t == "error":
                        print(f"  ! ERROR: {msg}")
                        break

                full_reply = "".join(assistant_chunks).strip()
                elapsed = time.time() - t0
                print(
                    f"--- TURN {turn_idx}: {bot.upper()} "
                    f"({elapsed:.1f}s, {len(turn_events)} events) ---"
                )
                print(full_reply or "(empty)")
                transcript.append({
                    "turn": turn_idx, "role": "assistant",
                    "text": full_reply, "events": turn_events,
                    "elapsed_s": round(elapsed, 2), "ts": time.time(),
                })
                # Small breath so the planner isn't slammed.
                await asyncio.sleep(0.5)

    finished_at = time.time()
    transcript_path.write_text(
        json.dumps(transcript, indent=2), encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps({
            "scenario": scenario_slug,
            "scenario_meta": meta,
            "bot": bot,
            "host": host,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_s": round(finished_at - started_at, 1),
            "user_turns": sum(1 for t in turns if t.get("role") == "user"),
        }, indent=2),
        encoding="utf-8",
    )
    print(f"<<< {scenario_slug} done in {finished_at - started_at:.1f}s")
    return run_dir


async def _run_all(
    *,
    host: str,
    token: str,
    out_root: Path,
    per_turn_timeout_s: float,
) -> list[Path]:
    runs: list[Path] = []
    for path in _scenario_paths():
        run_dir = await _run_one(
            scenario_path=path, host=host, token=token,
            out_root=out_root, per_turn_timeout_s=per_turn_timeout_s,
        )
        runs.append(run_dir)
    return runs


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", help="scenario id (e.g. 01) or filename")
    p.add_argument("--all", action="store_true", help="run all scenarios in order")
    p.add_argument("--list", action="store_true", help="list available scenarios")
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--token", help="bearer token (required unless --list)")
    p.add_argument(
        "--out", default="runs",
        help="output root (relative paths land under repo root)",
    )
    p.add_argument(
        "--per-turn-timeout", type=float, default=240.0,
        help="seconds to wait for a 'done' frame per turn",
    )
    args = p.parse_args(argv)

    if args.list:
        for path in _scenario_paths():
            meta, turns = _load_scenario(path)
            user_turns = sum(1 for t in turns if t.get("role") == "user")
            headline = meta.get("headline", "")
            print(f"  {path.stem}  [{user_turns} turns]  {headline}")
        return 0

    if not args.token:
        p.error("--token is required unless --list is given")

    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = Path(__file__).resolve().parent.parent / out_root

    if args.all:
        asyncio.run(_run_all(
            host=args.host, token=args.token, out_root=out_root,
            per_turn_timeout_s=args.per_turn_timeout,
        ))
        return 0

    if not args.scenario:
        p.error("either --scenario, --all, or --list must be given")

    path = _resolve_scenario(args.scenario)
    asyncio.run(_run_one(
        scenario_path=path, host=args.host, token=args.token,
        out_root=out_root, per_turn_timeout_s=args.per_turn_timeout,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
