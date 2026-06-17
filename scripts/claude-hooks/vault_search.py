#!/usr/bin/env python
"""Query the Ai-Team vault. Used by the `vault-search` skill.

Usage:
  vault_search.py "how does the vault-writer daemon work"
  vault_search.py "terry character prompt" -k 3
  vault_search.py "maggy" --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_AI_TEAM = Path(os.environ.get("HIVE_PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(_AI_TEAM))

VAULT = Path(os.environ.get("HIVE_VAULT_PATH", "./vault"))
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"


async def _embed(text: str) -> list[float]:
    import httpx
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=30.0) as client:
        r = await client.post(
            "/api/embeddings",
            # num_gpu=0 pins nomic-embed to CPU so it never competes
            # with planner-qwen for GPU VRAM. See
            # vault_writer/embedder.py for full rationale.
            json={
                "model": EMBED_MODEL,
                "prompt": text,
                "options": {"num_gpu": 0},
            },
        )
        r.raise_for_status()
        vec = r.json().get("embedding")
        if not isinstance(vec, list) or not vec or not all(isinstance(x, (int, float)) for x in vec):
            raise ValueError(f"unexpected embedding response: {str(r.text)[:200]}")
        return [float(x) for x in vec]


def main() -> int:
    ap = argparse.ArgumentParser(description="Search the Ai-Team vault.")
    ap.add_argument("query", help="natural-language query")
    ap.add_argument("-k", type=int, default=5, help="top-k results (default 5)")
    ap.add_argument("--json", action="store_true", help="emit JSON for tooling")
    args = ap.parse_args()

    try:
        vec = asyncio.run(_embed(args.query))
    except Exception as e:  # noqa: BLE001
        print(f"error: embedding failed: {e}", file=sys.stderr)
        print("hint: is ollama running, and nomic-embed-text pulled?", file=sys.stderr)
        return 1

    from shared.vault_client import VaultClient
    client = VaultClient(vault_path=VAULT, daemon_host="127.0.0.1", daemon_port=8765)
    results = client.search(query_embedding=vec, k=args.k, audience="claude-code")

    if args.json:
        print(json.dumps(
            [
                {
                    "path": r.path,
                    "score": round(r.score, 4),
                    "author": r.author,
                    "type": r.note_type,
                    "body": r.body,
                }
                for r in results
            ],
            indent=2,
        ))
        return 0

    if not results:
        print("(no results)")
        return 0

    for r in results:
        print(f"\n--- {r.path} | {r.note_type} | {r.author} | score={r.score:.3f}")
        preview = r.body.strip().replace("\n", " ")
        if len(preview) > 400:
            preview = preview[:400] + "..."
        print(preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
