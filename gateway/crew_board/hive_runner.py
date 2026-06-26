"""Hive runner — uses the existing HiveCoordinator (planner-qwen) to
process a task.

For MVP we treat the hive as a planning/proposal layer: it produces
a structured plan + reasoning that gets attached to the task as a
comment. Actual file edits are performed via the executor's
existing verbs when the synth emits them.

The runner is intentionally cheap. Tasks that genuinely need file
edits should escalate to the Claude Code runner after 2 hive
failures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gateway.crew_board.store import CrewBoardStore, Task

log = logging.getLogger("gateway.crew_board.hive")


@dataclass
class HiveResult:
    ok: bool
    plan_text: str
    actions_attempted: list[str]
    reason: str = ""


def _build_user_msg(task: Task) -> str:
    parts = [
        f"Task {task.slug}: {task.title}",
    ]
    if task.body:
        parts.append("")
        parts.append(task.body)
    if task.acceptance_criteria:
        parts.append("")
        parts.append("Acceptance criteria:")
        for c in task.acceptance_criteria:
            mark = "x" if c.get("checked") else " "
            parts.append(f"  [{mark}] {c.get('text', '')}")
    if task.files_of_interest:
        parts.append("")
        parts.append("Files of interest:")
        for g in task.files_of_interest:
            parts.append(f"  - {g}")
    parts.append("")
    parts.append(
        "Propose a concrete plan to satisfy the acceptance criteria. "
        "List the files to touch, the order of operations, and any "
        "external resources you'd need."
    )
    return "\n".join(parts)


async def run_hive(
    store: CrewBoardStore,
    task: Task,
    *,
    coordinator,
    device_id: str = "crew-board",
    user_id: int = 0,
) -> HiveResult:
    """Run a task through the existing HiveCoordinator. The result is
    captured as a structured comment on the task.

    `coordinator` is gateway.hive_coordinator.HiveCoordinator. We accept
    it as a parameter so tests can inject a fake.
    """
    from gateway.hive_coordinator import TurnContext
    from gateway.event_emitter import ListEmitter

    user_msg = _build_user_msg(task)
    ctx = TurnContext(
        user_msg=user_msg,
        user_id=user_id, device_id=device_id,
        bot="hive",
        available_helpers=[
            "planner", "researcher", "synthesizer", "critic",
        ],
    )
    em = ListEmitter()
    try:
        turn = await coordinator.coordinate(ctx, em)
    except Exception as e:  # noqa: BLE001
        log.exception("hive coordinator raised for task %s", task.slug)
        store.add_comment(
            task.slug, actor="hive",
            comment=f"runner crashed: {e}",
        )
        return HiveResult(
            ok=False, plan_text="", actions_attempted=[],
            reason=f"coordinator crashed: {e}",
        )
    reply = (turn.reply or "").strip()
    actions = [a.get("verb", "?") for a in (turn.actions or [])]
    store.add_comment(
        task.slug, actor="hive",
        comment=f"plan:\n{reply}\n\nactions attempted: {actions}",
    )
    # Heuristic OK: synth produced a non-trivial reply AND no error.
    ok = bool(reply) and turn.error is None and "trouble planning" not in reply.lower()
    return HiveResult(
        ok=ok,
        plan_text=reply,
        actions_attempted=actions,
        reason=turn.error or "",
    )
