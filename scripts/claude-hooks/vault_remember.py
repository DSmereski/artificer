#!/usr/bin/env python
"""Manual vault learn entry. Used by the `vault-remember` skill.

Usage:
  vault_remember.py knowledge "title" "body..."
  vault_remember.py system    "gpu layout" "GPUs 1,2 reserved for Terry"
  vault_remember.py project   "ai-team"   "Phase 2a complete, learn RPC live"
  vault_remember.py ops       "terse replies"  "david prefers no trailing summaries" --audience claude-code
  vault_remember.py tool      "vault-search"   "use when user says 'remember when'"

Author is always 'claude-code'.
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

_CATEGORIES = {"knowledge", "system", "project", "tool", "ops", "journal", "person"}
_DEFAULT_AUDIENCE = {
    "ops": ["claude-code"],
}


async def amain() -> int:
    ap = argparse.ArgumentParser(description="Write a note to the Ai-Team vault.")
    ap.add_argument("category", choices=sorted(_CATEGORIES))
    ap.add_argument("title")
    ap.add_argument("body")
    ap.add_argument("--audience", nargs="+", default=None,
                    help="audience tags (default: [all], or [claude-code] for ops)")
    ap.add_argument("--tags", nargs="+", default=None)
    args = ap.parse_args()

    from shared.vault_client import VaultClient
    client = VaultClient(vault_path=VAULT, daemon_host="127.0.0.1", daemon_port=8765)

    audience = args.audience or _DEFAULT_AUDIENCE.get(args.category, ["all"])
    tags = args.tags or []

    resp = await client.learn(
        category=args.category, title=args.title, body=args.body,
        author="claude-code", audience=audience, tags=tags,
    )
    if resp is None:
        print("error: vault-writer daemon unreachable at 127.0.0.1:8765",
              file=sys.stderr)
        return 1
    if "error" in resp:
        print(f"error: {resp['error']}", file=sys.stderr)
        return 1
    print(json.dumps(resp))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
