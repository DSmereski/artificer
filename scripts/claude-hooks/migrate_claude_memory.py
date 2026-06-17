#!/usr/bin/env python
"""One-time migration: ~/.claude/projects/*/memory/*.md -> Ai-Team vault.

Walks every Claude Code project's memory directory, reads each note's
frontmatter, maps the legacy `type` to a vault category, and sends the
entry through the vault-writer daemon's learn RPC.

After a successful send, the original file is MOVED (not deleted) to
~/.claude/projects/<project>/memory/_archived-YYYY-MM-DD/<original-name>.md
so nothing is lost and the migration is reversible.

Type mapping (per spec §8.6):
  user      -> person        (people/operator.md, audience: [all])
  feedback  -> ops           (ops/collaboration-<slug>.md, audience: [claude-code])
  project   -> project       (projects/<slug>.md, audience: [all])
  reference -> knowledge     (knowledge/YYYY/MM/<slug>.md, audience: [all])
  unknown   -> knowledge     (fallback)

Skip MEMORY.md index files; skip empty bodies; skip invalid frontmatter.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
import shutil
import sys
from pathlib import Path

import yaml

_AI_TEAM = Path(os.environ.get("HIVE_PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(_AI_TEAM))

MEMORY_ROOT = Path(os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")))
VAULT = Path(os.environ.get("HIVE_VAULT_PATH", "./vault"))

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_TYPE_MAP = {
    "user":      ("person",    ["all"]),
    "feedback":  ("ops",       ["claude-code"]),
    "project":   ("project",   ["all"]),
    "reference": ("knowledge", ["all"]),
}


def _parse(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw.strip()
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, raw[m.end():].strip()


def _title_from(fm: dict, path: Path) -> str:
    name = fm.get("name") or path.stem.replace("_", " ").replace("-", " ")
    return str(name)[:120]


async def _run() -> int:
    from shared.vault_client import VaultClient
    client = VaultClient(vault_path=VAULT, daemon_host="127.0.0.1", daemon_port=8765)

    if not await client.ping(timeout=2.0):
        print("ERROR: vault-writer daemon not reachable. Start it first.", file=sys.stderr)
        return 2

    today = dt.date.today().isoformat()
    archive_label = f"_archived-{today}"

    migrated = 0
    skipped = 0
    failed = 0

    for mem_file in sorted(MEMORY_ROOT.glob("*/memory/*.md")):
        if mem_file.name == "MEMORY.md":
            skipped += 1
            continue
        if mem_file.parent.name.startswith("_archived-"):
            skipped += 1
            continue

        fm, body = _parse(mem_file)
        if not body:
            print(f"[skip empty] {mem_file.relative_to(MEMORY_ROOT)}")
            skipped += 1
            continue

        legacy_type = str(fm.get("type", "")).lower()
        category, default_audience = _TYPE_MAP.get(
            legacy_type, ("knowledge", ["all"])
        )

        title = _title_from(fm, mem_file)
        tags = fm.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        # Add provenance tag so migrated entries are easy to find.
        tags = list(dict.fromkeys([*tags, "migrated-from-claude-memory"]))

        # Prepend description to body if present and missing from body.
        description = str(fm.get("description", "")).strip()
        if description and description.lower() not in body.lower():
            body_out = f"{description}\n\n{body}"
        else:
            body_out = body

        resp = await client.learn(
            category=category,
            title=title,
            body=body_out,
            author="claude-code",
            audience=default_audience,
            tags=tags,
        )
        if resp is None or ("error" in (resp or {})):
            print(f"[FAIL] {mem_file.relative_to(MEMORY_ROOT)}: "
                  f"{resp.get('error') if resp else 'daemon unreachable'}",
                  file=sys.stderr)
            failed += 1
            continue

        # Archive the original under a sibling dir.
        archive_dir = mem_file.parent / archive_label
        archive_dir.mkdir(exist_ok=True)
        dest = archive_dir / mem_file.name
        shutil.move(str(mem_file), str(dest))
        print(f"[ok {category:9s}] {mem_file.name:40s} -> {resp.get('path')}")
        migrated += 1

    print(f"\n=== migration complete ===")
    print(f"  migrated: {migrated}")
    print(f"  skipped:  {skipped}")
    print(f"  failed:   {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
