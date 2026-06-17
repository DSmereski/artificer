"""End-to-end conversation driver — pairs as a fake device, runs a
scripted multi-turn conversation through /v1/chat/terry, and dumps
every WS frame the gateway emits.

Usage:
    python scripts/e2e_chat_driver.py --token <token> [--host http://127.0.0.1:8766]
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


SCRIPT = [
    "Hey Terry, I'm running an end-to-end test. Please remember the codeword 'penguin-glacier' for later.",
    "What's 17 times 23?",
    "Can you describe a watercolor painting of a frosty pine forest at dawn? Don't generate it yet.",
    "Now tell me the codeword I gave you in my first message.",
    "Search the vault for anything about hive nodes.",
    "What was the multiplication answer you gave me earlier?",
    "Great. Summarize what we've talked about so far in three bullets.",
    "Final check — am I still talking with the same conversation context, yes or no?",
]


async def drive(host: str, token: str, out_path: Path) -> None:
    ws_host = host.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{ws_host}/v1/chat/terry"
    headers = {"Authorization": f"Bearer {token}"}
    transcript: list[dict] = []

    async with websockets.connect(url, additional_headers=headers, max_size=2**22) as ws:
        for turn_idx, user_text in enumerate(SCRIPT, 1):
            print(f"\n=== TURN {turn_idx}: USER ===")
            print(user_text)
            await ws.send(json.dumps({"type": "user", "text": user_text}))
            transcript.append({"turn": turn_idx, "role": "user", "text": user_text, "ts": time.time()})

            assistant_chunks: list[str] = []
            events: list[dict] = []
            t0 = time.time()
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=240)
                except asyncio.TimeoutError:
                    print(f"  ! TIMEOUT after {time.time()-t0:.1f}s")
                    events.append({"type": "_timeout", "after_s": time.time()-t0})
                    break
                msg = json.loads(raw)
                events.append(msg)
                t = msg.get("type")
                if t == "assistant":
                    assistant_chunks.append(msg.get("text", ""))
                elif t == "done":
                    break
                elif t == "error":
                    print(f"  ! ERROR: {msg}")
                    break
                elif t in ("system_notice",):
                    print(f"  [system_notice] {msg.get('text','')[:100]}")
                else:
                    pass  # collected silently
            full_reply = "".join(assistant_chunks).strip()
            elapsed = time.time() - t0
            print(f"--- TURN {turn_idx}: TERRY ({elapsed:.1f}s, {len(events)} events) ---")
            print(full_reply or "(empty)")
            transcript.append({
                "turn": turn_idx, "role": "assistant",
                "text": full_reply, "events": events,
                "elapsed_s": round(elapsed, 2), "ts": time.time(),
            })
            # Tiny breath so we don't slam the planner
            await asyncio.sleep(0.5)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(f"\nTranscript saved to: {out_path}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://127.0.0.1:8766")
    p.add_argument("--token", required=True)
    p.add_argument("--out", default="C:/tmp/ai-team/e2e_chat.json")
    args = p.parse_args(argv)
    asyncio.run(drive(args.host, args.token, Path(args.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
