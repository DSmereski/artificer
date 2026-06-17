"""Sync Claude Code skills into the shared vault skill store.

One canonical skill set across all agents: Claude Code authors skills in
`~/.claude/skills/<name>/SKILL.md`; this flattens each into
`<vault>/skills/<name>.md` — the store the gateway `/v1/skills` route
serves to the bots and the hive loop injects into task briefs. Run on
demand (or from a hook) to keep them in sync.

One-way (Claude Code -> vault). Skips unchanged files. Never deletes
vault skills that have no Claude Code source (hand-authored ones survive).
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLAUDE_SKILLS = Path.home() / ".claude" / "skills"
_VAULT_SKILLS = (
    Path.home() / "Ai-Team-Vault" / "skills"
)


def sync(claude_dir: Path = _CLAUDE_SKILLS,
         vault_dir: Path = _VAULT_SKILLS) -> tuple[int, int]:
    """Returns (written, skipped)."""
    vault_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for skill_md in sorted(claude_dir.glob("*/SKILL.md")):
        name = skill_md.parent.name
        src = skill_md.read_text(encoding="utf-8", errors="replace")
        dest = vault_dir / f"{name}.md"
        if dest.exists() and dest.read_text(encoding="utf-8",
                                            errors="replace") == src:
            skipped += 1
            continue
        dest.write_text(src, encoding="utf-8")
        written += 1
    return written, skipped


_HIVE_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
_VAULT_PROMPTS = Path.home() / "Ai-Team-Vault" / "prompts"


def sync_prompts(src_dir: Path = _HIVE_PROMPTS,
                 vault_dir: Path = _VAULT_PROMPTS) -> tuple[int, int]:
    """Mirror the hive helper-agent system prompts (gateway/prompts/*.md)
    into the vault so they're discoverable in Obsidian. One-way, same
    skip-unchanged semantics as sync()."""
    if not src_dir.is_dir():
        return 0, 0
    vault_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for md in sorted(src_dir.glob("*.md")):
        src = md.read_text(encoding="utf-8", errors="replace")
        dest = vault_dir / md.name
        if dest.exists() and dest.read_text(encoding="utf-8",
                                            errors="replace") == src:
            skipped += 1
            continue
        dest.write_text(src, encoding="utf-8")
        written += 1
    return written, skipped


if __name__ == "__main__":
    w, s = sync()
    print(f"skills sync: {w} written, {s} unchanged -> {_VAULT_SKILLS}")
    pw, ps = sync_prompts()
    print(f"prompts sync: {pw} written, {ps} unchanged -> {_VAULT_PROMPTS}")
    sys.exit(0)
