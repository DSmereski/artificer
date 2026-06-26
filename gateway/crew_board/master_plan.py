"""master_plan.py — CP2: Karpathy-structured master-plan drafting.

When the AI proposes work (Goal-decompose / Evolve "Go do more"), it no longer
dumps tickets straight into the build queue. Instead it drafts a MASTER PLAN
the human approves first. The plan follows the Karpathy method:

  - goal        : the decision/outcome the work drives (not the surface task)
  - assumptions : the key assumptions made (so the human can correct them)
  - open_questions : up to 5 questions whose answers would most change the plan
  - steps       : 3-7 checkpoints, biggest-value/lowest-risk first, each with a
                  MEASURABLE `verify` (a runnable check) + concrete check-offs

A single hive-qwen call drafts it; the human approves (breakout into tickets),
rejects (archive), or requests changes (re-draft with feedback). Pure-ish:
no DB writes here — callers persist the returned dict via store.set_plan_spec.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("crew_board.master_plan")

_PLAN_SYSTEM = """You are a planning agent using the Karpathy method. Given a
GOAL for a software project, produce a MASTER PLAN as JSON for a human to
APPROVE before any code is written. Apply the three layers:

- goal: ONE sentence — the decision/outcome the work drives, not the surface task.
- assumptions: the key assumptions you made (so the human can correct them).
- open_questions: up to 5 questions whose answers would MOST change the plan.
- steps: 3-7 CHECKPOINTS, biggest-value / lowest-risk first. Each step has:
    title    — what ships in this checkpoint
    why      — the goal it serves
    verify   — a MEASURABLE acceptance check that can be RUN (a test, a command,
               a probe) — never "looks good"
    criteria — 2-5 concrete check-offs (done = every one is true)

Keep it tight and concrete. Output ONLY the JSON object, no prose."""

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "verify": {"type": "string"},
                    "criteria": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "verify"],
            },
        },
    },
    "required": ["goal", "steps"],
}


async def draft_plan(
    store, slug: str, goal: str, *, feedback: str | None = None, invoker=None,
) -> dict:
    """Draft a Karpathy-structured master plan for *goal* on project *slug*.
    *feedback* (from a request-changes) is folded in to revise a prior draft.
    Returns a normalized {goal, assumptions, open_questions, steps[]} dict;
    `steps` may be empty if drafting failed (caller decides what to do)."""
    proj = store.get_project(slug)
    name = getattr(proj, "name", slug) if proj else slug

    repo_ctx = ""
    if proj is not None:
        try:
            from gateway.crew_board.evolve import _repo_signal
            repo_ctx = _repo_signal(Path(proj.path))[:4000]
        except Exception:  # noqa: BLE001
            repo_ctx = ""

    user = (
        f"Project: {name} (slug: {slug})\n\n"
        f"=== Code map + open markers ===\n{repo_ctx}\n\n"
        f"GOAL: {goal}\n"
    )
    if feedback:
        user += (
            "\nThe human reviewed your previous plan and asked for changes:\n"
            f"{feedback}\n\nRevise the plan accordingly — address the feedback "
            "and keep what still applies.\n"
        )
    user += "\nProduce the master plan as JSON."

    if invoker is None:
        from gateway.helpers.base import OllamaInvoker
        invoker = OllamaInvoker()

    try:
        from gateway.helpers.base import extract_json
        text, _, _ = await invoker.chat(
            model="hive-qwen", system=_PLAN_SYSTEM, user=user,
            params={"temperature": 0.4, "num_ctx": 8192, "num_predict": 2048},
            fmt=_PLAN_SCHEMA,
        )
        data = extract_json(text) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("master_plan: draft_plan failed for %s: %s", slug, e)
        data = {}

    steps: list[dict] = []
    for s in (data.get("steps") or []) if isinstance(data, dict) else []:
        if not isinstance(s, dict) or not str(s.get("title", "")).strip():
            continue
        steps.append({
            "title": str(s["title"]).strip()[:140],
            "why": str(s.get("why", "")).strip()[:400],
            "verify": str(s.get("verify", "")).strip()[:300],
            "criteria": [
                str(c).strip() for c in (s.get("criteria") or []) if str(c).strip()
            ][:5],
        })

    return {
        "goal": str((data or {}).get("goal", goal)).strip()[:300] or goal,
        "assumptions": [
            str(a).strip() for a in ((data or {}).get("assumptions") or []) if str(a).strip()
        ][:6],
        "open_questions": [
            str(q).strip() for q in ((data or {}).get("open_questions") or []) if str(q).strip()
        ][:5],
        "steps": steps[:8],
    }
