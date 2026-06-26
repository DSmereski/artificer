"""skills_suggest.py — CP2/#210: propose skill improvements from finished work.

When a task passes QA/review, the system can spot reusable patterns and suggest
either a NEW skill or an UPDATE to an existing one. Each suggestion lands in the
Proposed lane (tagged 'skill') for David's approval — feeding the
self-improvement loop (skills self-improve + sync_skills.py).

A single hive-qwen call, given the finished task + the list of existing skills,
returns suggestions. Pure-ish: no DB writes here — callers persist the result
by creating proposed tickets. Best-effort: returns [] on any failure so it can
never break the build pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("crew_board.skills_suggest")

_SKILLS_DIR = Path.home() / ".claude" / "skills"

_SYSTEM = """You decide whether a finished coding task should be captured as a
reusable SKILL (a documented, repeatable procedure the team reuses), or whether
it reveals that an EXISTING skill needs updating. Be conservative — most tasks
do NOT warrant a skill. Suggest one ONLY when the work embodies a genuinely
reusable pattern, workflow, or hard-won gotcha.

Output JSON: {"suggestions": [{"kind": "new" | "update", "skill": "<kebab-name>",
"why": "<one sentence: what the skill captures and why it's reusable>"}]}.
For "update", `skill` MUST be one of the existing skills listed. Empty list if
nothing is worth capturing."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["new", "update"]},
                    "skill": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["kind", "skill", "why"],
            },
        },
    },
    "required": ["suggestions"],
}


def _list_existing_skills() -> list[str]:
    """Names of installed skills (sub-dirs of ~/.claude/skills)."""
    try:
        return sorted(
            p.name for p in _SKILLS_DIR.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
    except Exception:  # noqa: BLE001
        return []


async def suggest_skills(store, task, *, invoker=None) -> list[dict]:
    """Suggest new/updated skills from a finished *task*. Returns a list of
    {kind, skill, why} (possibly empty). Never raises."""
    existing = _list_existing_skills()
    summary = (getattr(task, "last_summary", "") or task.body or "")[:600]
    user = (
        f"A coding task just passed review:\n"
        f"Title: {task.title}\n"
        f"Project: {task.project_slug}\n"
        f"What was done: {summary}\n\n"
        f"Existing skills ({len(existing)}): {', '.join(existing) or '(none)'}\n\n"
        f"Should we capture a reusable skill from this, or update an existing "
        f"one? Output JSON (empty list if not worth it)."
    )

    if invoker is None:
        from gateway.helpers.base import OllamaInvoker
        invoker = OllamaInvoker()

    try:
        from gateway.helpers.base import extract_json
        text, _, _ = await invoker.chat(
            model="hive-qwen", system=_SYSTEM, user=user,
            params={"temperature": 0.3, "num_ctx": 8192, "num_predict": 1024},
            fmt=_SCHEMA,
        )
        data = extract_json(text) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("skills_suggest: failed for %s: %s", getattr(task, "slug", "?"), e)
        return []

    raw = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    existing_set = set(existing)
    for s in raw:
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind", "")).strip()
        skill = str(s.get("skill", "")).strip()[:60]
        why = str(s.get("why", "")).strip()[:300]
        if kind not in ("new", "update") or not skill or not why:
            continue
        # An "update" must name a real skill; otherwise treat as new.
        if kind == "update" and skill not in existing_set:
            kind = "new"
        out.append({"kind": kind, "skill": skill, "why": why})
    return out[:4]
