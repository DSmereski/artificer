"""Per-task markdown mirror under vault/tasks/<slug>.md.

Canonical state lives in SQLite (CrewBoardStore). The mirror is a
read-only view for Obsidian. The frontmatter says so. Edits via
Obsidian are NOT picked up — owner edits via the web UI or chat.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from gateway.crew_board.store import Task

log = logging.getLogger("gateway.crew_board.mirror")


def _format_task_md(task: Task) -> str:
    fm = {
        "type": "crew_task",
        "slug": task.slug,
        "title": task.title,
        "status": task.status,
        "project": task.project_slug,
        "assignee": task.assignee,
        "created_by": task.created_by,
        "priority": task.priority,
        "estimate": task.estimate,
        "tags": task.tags,
        "depends_on": task.depends_on,
        "attempt_count": task.attempt_count,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "READ_ONLY": (
            "Canonical state is in SQLite. Edit via /board or chat."
        ),
    }
    fm_yaml = "\n".join(
        f"{k}: {json.dumps(v) if isinstance(v, (list, dict, bool)) else v}"
        if v is not None else f"{k}: null"
        for k, v in fm.items()
    )
    parts = [
        "---",
        fm_yaml,
        "---",
        "",
        f"# {task.title}",
        "",
    ]
    if task.body:
        parts.extend([task.body, ""])
    if task.acceptance_criteria:
        parts.append("## Acceptance criteria")
        parts.append("")
        for c in task.acceptance_criteria:
            mark = "x" if c.get("checked") else " "
            parts.append(f"- [{mark}] {c.get('text', '')}")
        parts.append("")
    if task.files_of_interest:
        parts.append("## Files of interest")
        parts.append("")
        for g in task.files_of_interest:
            parts.append(f"- `{g}`")
        parts.append("")
    if task.verify_results:
        parts.append("## Verify results")
        parts.append("")
        parts.append("```json")
        parts.append(json.dumps(task.verify_results, indent=2))
        parts.append("```")
        parts.append("")
    return "\n".join(parts)


def mirror_task(task: Task, vault_path: Path) -> Path:
    """Write `vault_path/tasks/<slug>.md`. Returns the absolute path."""
    out_dir = vault_path / "tasks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{task.slug}.md"
    text = _format_task_md(task)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def mirror_lessons(store, project_slug: str, vault_path: Path) -> Path | None:
    """Write the project's distilled hive lessons to
    `vault/knowledge/crew-lessons-<project>.md` so the knowledge the hive
    learns lives in Obsidian, not just the SQLite `crew_lessons` table.
    Best-effort; returns the path written or None."""
    try:
        lessons = store.recent_lessons(project_slug, limit=100)
    except Exception:  # noqa: BLE001
        return None
    out_dir = vault_path / "knowledge"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"crew-lessons-{project_slug}.md"
    lines = [
        "---",
        f"title: Crew lessons — {project_slug}",
        "source: crew_board (auto-generated; edit via the pipeline)",
        "tags: [crew, lessons, hive]",
        "---",
        "",
        f"# Hive lessons — {project_slug}",
        "",
        "Distilled after Claude rescued a task the hive couldn't finish. "
        "Seeded into future task briefs so the hive avoids repeats.",
        "",
    ]
    if not lessons:
        lines.append("_No lessons recorded yet._")
    for i, ls in enumerate(lessons, 1):
        tags = ", ".join(getattr(ls, "tags", []) or [])
        head = f"## {i}. {getattr(ls, 'task_slug', '') or 'lesson'}"
        if tags:
            head += f"  ({tags})"
        lines.append(head)
        lines.append(getattr(ls, "body", "").strip())
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def remove_mirror(slug: str, vault_path: Path) -> None:
    p = vault_path / "tasks" / f"{slug}.md"
    if p.exists():
        try:
            p.unlink()
        except OSError as e:
            log.warning("mirror unlink failed for %s: %s", slug, e)
