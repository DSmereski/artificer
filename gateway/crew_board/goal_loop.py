"""P6 — Goal-completion verification loop.

A decomposed goal can't be falsely "done". When all its subtasks reach
done/archived, the hive auto-spawns a VERIFY ticket that reviews the
codebase against the goal's acceptance checklist. Unmet items spawn a
bounded re-goal; a 3-cycle hard cap then escalates to David.

Storage: goal records live in ``crew_meta`` as JSON values under keys
``goal:<goal_id>``. No new tables — reuses the existing set_meta/get_meta
pattern. Subtasks carry the goal_id in their ``tags`` list as
``goal:<goal_id>`` (no schema change needed; tags is already TEXT JSON).

Hard cap: a HARD counter at the code level (``GOAL_MAX_CYCLES = 3``).
The cap is checked in Python, not a prompt, so it cannot be overridden
by any model output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("gateway.crew_board.goal_loop")

# Maximum number of re-goal cycles before escalating to the owner.
# This is a HARD counter in code — never a prompt instruction.
GOAL_MAX_CYCLES = 3

# Tag prefix used to stamp subtasks and verify tickets with their goal_id.
_GOAL_TAG_PREFIX = "goal:"
_VERIFY_TAG = "goal-verify"

# crew_meta key template for a goal record.
_META_KEY = "goal:{goal_id}"


# --------------------------------------------------------------------------- #
#  Goal record                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class GoalRecord:
    goal_id: str
    text: str
    project_slug: str
    checklist: list[dict]   # [{item: str, met: bool}, ...]
    cycle: int = 0
    status: str = "active"  # "active" | "complete" | "needs_you"
    verify_spawned: bool = False  # per-cycle guard: prevents double-spawn

    def to_json(self) -> str:
        return json.dumps({
            "goal_id": self.goal_id,
            "text": self.text,
            "project_slug": self.project_slug,
            "checklist": self.checklist,
            "cycle": self.cycle,
            "status": self.status,
            "verify_spawned": self.verify_spawned,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "GoalRecord":
        d = json.loads(raw)
        return cls(
            goal_id=d["goal_id"],
            text=d["text"],
            project_slug=d["project_slug"],
            checklist=d.get("checklist") or [],
            cycle=int(d.get("cycle", 0)),
            status=str(d.get("status", "active")),
            verify_spawned=bool(d.get("verify_spawned", False)),
        )


def _meta_key(goal_id: str) -> str:
    return _META_KEY.format(goal_id=goal_id)


def store_goal(store, goal: GoalRecord) -> None:
    """Persist a GoalRecord into crew_meta."""
    store.set_meta(_meta_key(goal.goal_id), goal.to_json())


def get_goal(store, goal_id: str) -> GoalRecord | None:
    """Retrieve a GoalRecord from crew_meta. Returns None if absent."""
    raw = store.get_meta(_meta_key(goal_id))
    if raw is None:
        return None
    try:
        return GoalRecord.from_json(raw)
    except (json.JSONDecodeError, KeyError) as e:
        log.warning("goal_loop: corrupt goal record %s: %s", goal_id, e)
        return None


def create_goal(
    store,
    *,
    text: str,
    project_slug: str,
    checklist_items: list[str],
    cycle: int = 0,
    goal_id: str | None = None,
) -> GoalRecord:
    """Create and persist a new GoalRecord. Returns the record."""
    gid = goal_id or str(uuid.uuid4())[:8]
    checklist = [{"item": item, "met": False} for item in checklist_items]
    goal = GoalRecord(
        goal_id=gid,
        text=text,
        project_slug=project_slug,
        checklist=checklist,
        cycle=cycle,
        status="active",
        verify_spawned=False,
    )
    store_goal(store, goal)
    return goal


def goal_tag(goal_id: str) -> str:
    """The tag string stamped onto every subtask belonging to a goal."""
    return f"{_GOAL_TAG_PREFIX}{goal_id}"


def extract_goal_id(tags: list[str]) -> str | None:
    """Extract goal_id from a task's tags list. Returns None if not present."""
    for t in (tags or []):
        if t.startswith(_GOAL_TAG_PREFIX) and t != _VERIFY_TAG:
            return t[len(_GOAL_TAG_PREFIX):]
    return None


# --------------------------------------------------------------------------- #
#  Completion trigger                                                            #
# --------------------------------------------------------------------------- #

def _all_subtasks_done(store, goal_id: str) -> bool:
    """Return True when ALL tasks stamped with goal_id are done or archived,
    excluding verify tickets (which have the 'goal-verify' tag).

    Uses the goal_id column index (fast O(1) query) rather than scanning
    all tasks for the tag.
    """
    tasks = store.list_tasks_by_goal_id(goal_id)
    subtasks = [
        t for t in tasks
        if _VERIFY_TAG not in (t.tags or [])
    ]
    if not subtasks:
        return False  # nothing created yet; not done
    return all(t.status in ("done", "archived") for t in subtasks)


def maybe_spawn_verify(store, goal_id: str) -> bool:
    """Check if all subtasks of *goal_id* are done. If so, and no verify
    ticket has been spawned this cycle, create one and mark the guard.

    Returns True if a verify ticket was created, False otherwise.
    Idempotent: calling it multiple times for the same cycle is safe.
    """
    goal = get_goal(store, goal_id)
    if goal is None:
        log.debug("goal_loop: unknown goal_id %s", goal_id)
        return False
    if goal.status != "active":
        return False  # complete or already escalated
    if goal.verify_spawned:
        return False  # idempotency guard — already spawned this cycle
    if not _all_subtasks_done(store, goal_id):
        return False

    # All subtasks done and no verify ticket yet — spawn one.
    goal.verify_spawned = True
    store_goal(store, goal)

    from gateway.crew_board import schema as _schema
    verify_task = store.create_task(
        title=f"[goal-verify] cycle={goal.cycle} {goal.text[:60]}",
        project_slug=goal.project_slug,
        body=(
            f"Verify goal (cycle {goal.cycle}): {goal.text}\n\n"
            f"Checklist:\n"
            + "\n".join(f"- [ ] {c['item']}" for c in goal.checklist)
        ),
        created_by="system",
        tags=[_VERIFY_TAG, goal_tag(goal_id)],
        goal_id=goal_id,
        acceptance_criteria=[
            {"text": f"checklist item met: {c['item']}", "checked": False}
            for c in goal.checklist
        ],
    )
    # Move to ready so the dispatcher picks it up.
    # Path: proposed → backlog → ready (the state machine requires this sequence
    # for system-created tasks, which land in proposed by default).
    store.assign_task(verify_task.slug, "hive", actor="system")
    store.move_task(
        verify_task.slug, _schema.STATUS_BACKLOG,
        actor="system", detail="goal-verify ticket created",
    )
    store.move_task(
        verify_task.slug, _schema.STATUS_READY,
        actor="system", detail="goal-verify ticket auto-spawned",
    )
    log.info(
        "goal_loop: spawned verify ticket %s for goal %s (cycle=%d)",
        verify_task.slug, goal_id, goal.cycle,
    )
    return True


# --------------------------------------------------------------------------- #
#  Verify runner                                                                 #
# --------------------------------------------------------------------------- #

async def run_goal_verify(
    store,
    verify_task,
    *,
    qwen_invoker=None,
    claude_runner=None,
) -> dict[str, Any]:
    """Run the goal-verification step.

    Strategy:
    1. Ask planner-qwen to evaluate each checklist item against a bounded
       repo-map (file list + goal text as the lens). Returns per-item
       MET/UNMET/UNCERTAIN.
    2. If any UNMET or UNCERTAIN items remain, escalate to Claude for a
       binding verdict.
    3. Write the met/unmet verdicts back onto the goal record.
    4. Post a report comment onto the verify task.
    5. Return {"all_met": bool, "verdicts": [{item, met, reason}, ...]}.

    Both model calls are mockable via *qwen_invoker* / *claude_runner*
    for unit tests.
    """
    goal_id = extract_goal_id(verify_task.tags or [])
    if goal_id is None:
        log.warning("goal_loop: verify task %s has no goal tag", verify_task.slug)
        return {"all_met": False, "verdicts": [], "error": "no goal_id tag"}

    goal = get_goal(store, goal_id)
    if goal is None:
        return {"all_met": False, "verdicts": [], "error": f"goal {goal_id} not found"}

    project = store.get_project(goal.project_slug)
    project_path = project.path if project else "unknown"

    # Build a bounded repo-map (file list — NOT full file contents).
    repo_map = _build_repo_map(project_path)

    checklist_text = "\n".join(
        f"- {c['item']}" for c in goal.checklist
    )

    # Phase 1: qwen-first evaluation (fast, free).
    qwen_prompt = (
        f"Goal: {goal.text}\n\n"
        f"Checklist (evaluate each against the codebase):\n{checklist_text}\n\n"
        f"Repo file tree (lens — judge each checklist item by what MUST exist):\n"
        f"{repo_map}\n\n"
        f"For each checklist item output exactly this JSON array:\n"
        f'[{{"item": "...", "verdict": "MET"|"UNMET"|"UNCERTAIN", "reason": "..."}}]\n'
        f"Be concise. Do not make up files."
    )

    qwen_verdicts: list[dict] = []
    if qwen_invoker is not None:
        try:
            raw = await qwen_invoker(qwen_prompt)
            qwen_verdicts = _parse_verdicts(raw, goal.checklist)
        except Exception as e:  # noqa: BLE001
            log.warning("goal_loop: qwen verify failed for %s: %s", goal_id, e)

    # Phase 2: Claude escalation for any UNMET or UNCERTAIN.
    needs_claude = any(
        v.get("verdict") in ("UNMET", "UNCERTAIN")
        for v in qwen_verdicts
    ) or not qwen_verdicts

    final_verdicts = list(qwen_verdicts)
    if needs_claude and claude_runner is not None:
        unmet_items = [
            v for v in qwen_verdicts if v.get("verdict") != "MET"
        ] or [{"item": c["item"]} for c in goal.checklist]
        unmet_text = "\n".join(f"- {v['item']}" for v in unmet_items)
        claude_prompt = (
            f"Goal: {goal.text}\n\n"
            f"These checklist items are UNMET or UNCERTAIN:\n{unmet_text}\n\n"
            f"Repo file tree:\n{repo_map}\n\n"
            f"Project path: {project_path}\n\n"
            f"For each listed item, give a BINDING verdict:\n"
            f'[{{"item": "...", "verdict": "MET"|"UNMET", "reason": "..."}}]\n'
            f"Claude's verdict wins over qwen's. Be rigorous."
        )
        try:
            raw_claude = await claude_runner(claude_prompt)
            claude_verdicts = _parse_verdicts(raw_claude, goal.checklist)
            # Merge: Claude's verdict overrides qwen's for items it addressed.
            claude_by_item = {v["item"]: v for v in claude_verdicts}
            for i, v in enumerate(final_verdicts):
                if v["item"] in claude_by_item:
                    final_verdicts[i] = claude_by_item[v["item"]]
            # Items Claude addressed but not in qwen's output at all.
            covered = {v["item"] for v in final_verdicts}
            for v in claude_verdicts:
                if v["item"] not in covered:
                    final_verdicts.append(v)
        except Exception as e:  # noqa: BLE001
            log.warning("goal_loop: claude verify failed for %s: %s", goal_id, e)

    # If we still have no verdicts (both model calls skipped/failed), mark
    # all items as UNMET so the re-goal path triggers correctly.
    if not final_verdicts:
        final_verdicts = [
            {"item": c["item"], "verdict": "UNMET", "reason": "verify skipped"}
            for c in goal.checklist
        ]

    all_met = all(v.get("verdict") == "MET" for v in final_verdicts)

    # Write verdicts back onto the goal record.
    new_checklist = []
    verdict_by_item = {v["item"]: v for v in final_verdicts}
    for c in goal.checklist:
        verdict = verdict_by_item.get(c["item"], {})
        new_checklist.append({
            "item": c["item"],
            "met": verdict.get("verdict") == "MET",
            "reason": verdict.get("reason", ""),
        })
    goal.checklist = new_checklist
    store_goal(store, goal)

    # Post a report comment onto the verify task.
    report_lines = [f"Goal-verify report (cycle {goal.cycle}):"]
    for v in final_verdicts:
        icon = "MET" if v.get("verdict") == "MET" else "UNMET"
        report_lines.append(f"  [{icon}] {v['item']}: {v.get('reason', '')}")
    store.add_comment(
        verify_task.slug, actor="system",
        comment="\n".join(report_lines),
    )

    return {"all_met": all_met, "verdicts": final_verdicts}


def _build_repo_map(project_path: str, max_lines: int = 80) -> str:
    """Build a bounded file-tree string for the project path.
    Limited to max_lines so the prompt stays cheap."""
    import os
    lines: list[str] = []
    try:
        for root, dirs, files in os.walk(project_path):
            # Skip hidden dirs and common noise.
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in ("__pycache__", "node_modules", ".git")
            ]
            rel = os.path.relpath(root, project_path)
            prefix = "" if rel == "." else rel + "/"
            for f in sorted(files)[:20]:  # cap files per dir
                lines.append(f"{prefix}{f}")
                if len(lines) >= max_lines:
                    lines.append("... (truncated)")
                    return "\n".join(lines)
    except OSError:
        return "(repo map unavailable)"
    return "\n".join(lines) or "(empty)"


def _parse_verdicts(raw: str, checklist: list[dict]) -> list[dict]:
    """Parse model output into [{item, verdict, reason}].
    Best-effort: falls back to UNCERTAIN if the output is unparseable."""
    import re as _re
    # Try to find a JSON array in the output.
    match = _re.search(r"\[[\s\S]*?\]", raw or "")
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list) and all(isinstance(d, dict) for d in parsed):
                out = []
                for d in parsed:
                    verdict = str(d.get("verdict", "UNCERTAIN")).upper()
                    if verdict not in ("MET", "UNMET", "UNCERTAIN"):
                        verdict = "UNCERTAIN"
                    out.append({
                        "item": str(d.get("item", "?")),
                        "verdict": verdict,
                        "reason": str(d.get("reason", "")),
                    })
                return out
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: mark all as UNCERTAIN.
    return [
        {"item": c["item"], "verdict": "UNCERTAIN", "reason": "parse failed"}
        for c in checklist
    ]


# --------------------------------------------------------------------------- #
#  Gap → bounded re-goal                                                        #
# --------------------------------------------------------------------------- #

async def handle_verify_result(
    store,
    verify_task,
    verify_result: dict,
    *,
    notifier=None,
    decompose_fn=None,
) -> None:
    """Post-verify: close the goal or launch a bounded re-goal.

    Called after run_goal_verify returns.

    - all_met = True  → goal status=complete, verify task → done.
    - UNMET AND cycle < GOAL_MAX_CYCLES (3) → new goal (cycle+1) via
      decompose_fn; new subtasks carry the incremented goal.
    - UNMET AND cycle >= GOAL_MAX_CYCLES  → STOP, status=needs_you,
      fire escalation. NEVER create a 4th cycle. This is the hard cap.
    """
    from gateway.crew_board import schema as _schema

    goal_id = extract_goal_id(verify_task.tags or [])
    if goal_id is None:
        log.warning("handle_verify_result: no goal_id on %s", verify_task.slug)
        return

    goal = get_goal(store, goal_id)
    if goal is None:
        return

    all_met = bool(verify_result.get("all_met"))

    if all_met:
        # Success: close the goal.
        goal.status = "complete"
        store_goal(store, goal)
        _move_verify_done(store, verify_task)
        _notify(notifier, "goal_complete", verify_task.slug, goal_id=goal_id)
        log.info(
            "goal_loop: goal %s complete (cycle=%d)", goal_id, goal.cycle
        )
        return

    # Some items unmet.
    unmet = [
        v["item"]
        for v in (verify_result.get("verdicts") or [])
        if v.get("verdict") != "MET"
    ]
    if not unmet:
        # No verdict detail — treat as all UNMET.
        unmet = [c["item"] for c in goal.checklist]

    # HARD CAP: check BEFORE creating anything.
    if goal.cycle >= GOAL_MAX_CYCLES:
        # cycle is already at (or past) cap — STOP. No 4th cycle ever.
        goal.status = "needs_you"
        store_goal(store, goal)
        _move_verify_done(store, verify_task)
        _fire_escalation(store, goal, unmet, notifier=notifier)
        log.warning(
            "goal_loop: HARD CAP reached for goal %s (cycle=%d >= %d). "
            "Escalating to owner. NO re-goal created.",
            goal_id, goal.cycle, GOAL_MAX_CYCLES,
        )
        return

    # cycle < GOAL_MAX_CYCLES — create a re-goal (cycle+1).
    new_cycle = goal.cycle + 1
    re_text = f"finish unmet items (re-goal cycle {new_cycle}): " + "; ".join(unmet)
    new_goal = create_goal(
        store,
        text=re_text,
        project_slug=goal.project_slug,
        checklist_items=unmet,
        cycle=new_cycle,
        goal_id=f"{goal_id}-c{new_cycle}",
    )

    # Close the original goal (superseded by re-goal).
    goal.status = "active"   # keep active so we can query via re-goal chain
    store_goal(store, goal)

    _move_verify_done(store, verify_task)
    _notify(
        notifier, "goal_regoal", verify_task.slug,
        goal_id=goal_id, new_goal_id=new_goal.goal_id, cycle=new_cycle,
    )
    log.info(
        "goal_loop: goal %s cycle=%d UNMET — created re-goal %s (cycle=%d)",
        goal_id, goal.cycle, new_goal.goal_id, new_cycle,
    )

    # Decompose the re-goal into subtasks if a decompose_fn is provided.
    if decompose_fn is not None:
        try:
            await decompose_fn(
                goal_id=new_goal.goal_id,
                text=re_text,
                project_slug=new_goal.project_slug,
                checklist_items=unmet,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "goal_loop: re-goal decompose failed for %s: %s",
                new_goal.goal_id, e,
            )


def _move_verify_done(store, verify_task) -> None:
    """Move the verify ticket to done along the legal state-machine path.

    The task may be at various statuses when this is called (the test
    calls handle_verify_result directly without running the dispatcher).
    Walk the minimum-transition path to reach done from wherever it is.
    """
    from gateway.crew_board import schema as _schema
    try:
        t = store.get_task(verify_task.slug)
        if t is None:
            return
        # Walk forward through the machine to reach done.
        # proposed → backlog → ready → in_progress → qa → review → done
        path = [
            _schema.STATUS_BACKLOG,
            _schema.STATUS_READY,
            _schema.STATUS_IN_PROGRESS,
            _schema.STATUS_QA,
            _schema.STATUS_REVIEW,
            _schema.STATUS_DONE,
        ]
        for step in path:
            t = store.get_task(verify_task.slug)
            if t is None:
                return
            if t.status == _schema.STATUS_DONE:
                return
            if step in _schema.ALLOWED_TRANSITIONS.get(t.status, frozenset()):
                store.move_task(verify_task.slug, step,
                                actor="system", detail="goal-verify close")
    except Exception as e:  # noqa: BLE001
        log.warning("goal_loop: could not close verify task %s: %s",
                    verify_task.slug, e)


def _fire_escalation(store, goal: GoalRecord, unmet: list[str], *, notifier=None) -> None:
    """Create a needs-you escalation ticket + notify."""
    unmet_text = "; ".join(unmet[:10])
    try:
        from gateway.crew_board import schema as _schema
        esc = store.create_task(
            title=f"[needs-you] Goal {goal.goal_id} stuck after {GOAL_MAX_CYCLES} cycles",
            project_slug=goal.project_slug,
            body=(
                f"Goal: {goal.text}\n\n"
                f"After {GOAL_MAX_CYCLES} verification cycles the following items "
                f"remain unmet:\n"
                + "\n".join(f"- {item}" for item in unmet[:20])
                + "\n\nThis goal has been stopped. Owner review required."
            ),
            created_by="system",
            tags=["needs-you", f"goal:{goal.goal_id}"],
        )
        # Leave in backlog for owner to review.
    except Exception as e:  # noqa: BLE001
        log.warning("goal_loop: escalation ticket creation failed: %s", e)
    _notify(notifier, "goal_needs_you", "", goal_id=goal.goal_id, unmet=unmet_text)


def _notify(notifier, event: str, slug: str, **extra) -> None:
    if notifier is None:
        return
    try:
        notifier.broadcast({"event": event, "task": slug, **extra})
    except Exception:  # noqa: BLE001
        log.exception("goal_loop: notifier broadcast failed")


# --------------------------------------------------------------------------- #
#  Decompose helper called from the re-goal path                                #
# --------------------------------------------------------------------------- #

async def regoal_decompose(
    store,
    *,
    goal_id: str,
    text: str,
    project_slug: str,
    checklist_items: list[str],
) -> None:
    """Create ready subtasks for a re-goal using planner-qwen to plan them.

    Each new subtask gets tagged with the re-goal's goal_id so the
    completion trigger can group them correctly on the next cycle.

    This is a lightweight plan-only call — if the invoker fails, the
    re-goal's verify_spawned=False means the next tick will retry.
    """
    from gateway.crew_board import schema as _schema
    from gateway.helpers.base import OllamaInvoker, extract_json

    goal = get_goal(store, goal_id)
    if goal is None:
        return

    unmet_list = "\n".join(f"- {item}" for item in checklist_items)
    system = (
        "You are a task planner. Given a list of unmet checklist items, "
        "output a JSON array of 1-4 subtasks: "
        '[{"title": "...", "body": "..."}]. Respond with JSON only.'
    )
    user = (
        f"Project: {project_slug}\n"
        f"Unmet items to fix:\n{unmet_list}\n\n"
        "Break this into 1-4 focused subtasks."
    )
    try:
        raw, _, _ = await OllamaInvoker().chat(
            model="planner-qwen", system=system, user=user,
            params={"temperature": 0.3, "num_ctx": 4096, "num_predict": 1024},
        )
        tickets = extract_json(raw)
        if not isinstance(tickets, list):
            tickets = [{"title": f"Fix unmet items: {text[:80]}", "body": unmet_list}]
    except Exception as e:  # noqa: BLE001
        log.warning("regoal_decompose: planner failed: %s", e)
        tickets = [{"title": f"Fix unmet items: {text[:80]}", "body": unmet_list}]

    tag = goal_tag(goal_id)
    for tk in tickets[:4]:
        title = str(tk.get("title", "re-goal fix"))[:120]
        task = store.create_task(
            title=title,
            project_slug=project_slug,
            body=str(tk.get("body", "")),
            created_by="system",
            tags=["nl-decompose", "re-goal", tag],
            goal_id=goal_id,
        )
        store.assign_task(task.slug, "hive", actor="system")
        # proposed → backlog → ready (system-created tasks land in proposed).
        store.move_task(
            task.slug, _schema.STATUS_BACKLOG,
            actor="system", detail="re-goal cycle subtask created",
        )
        store.move_task(
            task.slug, _schema.STATUS_READY,
            actor="system", detail="re-goal cycle subtask queued",
        )
    log.info(
        "regoal_decompose: created %d subtasks for re-goal %s",
        len(tickets[:4]), goal_id,
    )
