#!/usr/bin/env python
"""SessionStart hook: load vault context into Claude Code sessions.

Reads context from the Ai-Team vault via VaultClient (filtered to
audience=claude-code) and emits it as additionalContext JSON.

Fail-soft: any error returns exit 0 with no output so Claude Code
startup is never blocked.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_AI_TEAM = Path(os.environ.get("HIVE_PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(_AI_TEAM))

VAULT = Path(os.environ.get("HIVE_VAULT_PATH", "./vault"))


def main() -> int:
    try:
        from shared.vault_client import VaultClient
        from vault_writer.util import wrap_untrusted
    except Exception as e:  # noqa: BLE001
        print(f"vault_preload: import failed: {e}", file=sys.stderr)
        return 0

    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        client = VaultClient(
            vault_path=VAULT, daemon_host="127.0.0.1", daemon_port=8765
        )
        context = client.preload_for_claude_code(cwd=cwd)
    except Exception as e:  # noqa: BLE001
        print(f"vault_preload: runtime failed: {e}", file=sys.stderr)
        return 0

    if not context.strip():
        return 0

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": wrap_untrusted(context, source="vault"),
        }
    }
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
