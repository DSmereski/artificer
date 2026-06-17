"""One-shot scaffolder: extract bot character prompts into the vault's canon/ files.

Run: python scripts/seed-vault.py

Reads from:
  bots/maggy/bot.py (attribute _MAGGY_SYSTEM)
  shared/llm_client.py (attribute _DEFAULT_SYSTEM - Terry's default)
  bots/scout/bot.py (attribute _SCOUT_SYSTEM_BASE)

Writes to: <vault>/canon/{maggy,terry,scout}.md

Idempotent - safe to re-run; overwrites canon files only if the extracted
content differs from what's on disk.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import os as _os
PROJECT = Path(_os.environ.get("HIVE_PROJECT_ROOT", str(Path(__file__).resolve().parents[1])))
VAULT   = Path(_os.environ.get("HIVE_VAULT_PATH", "./vault"))

# Map bot name -> (module path relative to PROJECT, attribute name holding the character prompt).
BOTS = {
    "maggy": (PROJECT / "bots" / "maggy" / "bot.py",   "_MAGGY_SYSTEM"),
    "terry": (PROJECT / "shared" / "llm_client.py",    "_DEFAULT_SYSTEM"),
    "scout": (PROJECT / "bots" / "scout" / "bot.py",   "_SCOUT_SYSTEM_BASE"),
}

TEMPLATE = """\
---
type: canon
author: human
audience: [all]
created: {created}
updated: {updated}
tags: [character, bot]
source: {source}
seeded: {seeded}
---

# {title}

{body}
"""


def load_attr(path: Path, attr: str) -> str:
    # Add PROJECT to sys.path so relative imports in the target module work.
    if str(PROJECT) not in sys.path:
        sys.path.insert(0, str(PROJECT))
    spec = importlib.util.spec_from_file_location(path.stem + "_" + attr, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, attr):
        raise AttributeError(f"{path} has no attribute {attr!r}")
    return getattr(mod, attr)


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    today = dt.date.today().isoformat()
    rc = 0
    for bot, (path, attr) in BOTS.items():
        canon = VAULT / "canon" / f"{bot}.md"
        try:
            body = load_attr(path, attr).strip()
        except Exception as e:  # noqa: BLE001
            print(f"[seed-vault] SKIP {bot}: {e}", file=sys.stderr)
            rc = 1
            continue
        rel_source = path.relative_to(PROJECT).as_posix()
        content = TEMPLATE.format(
            created=now, updated=now,
            source=f"{rel_source} ({attr})",
            seeded=today,
            title=bot.capitalize(),
            body=body,
        )
        existing = canon.read_text(encoding="utf-8") if canon.exists() else ""
        if existing == content:
            print(f"[seed-vault] unchanged: {canon}")
            continue
        canon.write_text(content, encoding="utf-8")
        print(f"[seed-vault] wrote: {canon}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
