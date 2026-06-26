"""CrewBoardManager — autonomous daemon that manages the crew board.

Decomposes goals, assigns agents, triages swimlanes, tracks progress,
vets outputs, escalates stale items, and reports completion. Runs as a
background event loop alongside CrewDispatcher.

Triggers: WS /board/events (real-time) + 60s poll for stale detection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore
from gateway.helpers.base import extract_json

log = logging.getLogger("gateway.crew_board.manager_daemon")


class BoardDecision:
    """Structured output from the daemon's LLM decision engine."""

    __slots__ = ("action", "kwargs")

    def __init__(self, action: str, **kwargs: Any) -> None:
        self.action = action
        self.kwargs = kwargs

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, **self.kwargs}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BoardDecision | None:
        if not isinstance(d, dict) or "action" not in d:
            return None
        action = d.pop("action")
        if action not in _VALID_ACTIONS:
            return None
        return cls(action, **d)


_VALID_ACTIONS = frozenset({"decompose", "triage", "assign", "vet", "escalate", "close"})


def _load_system_prompt() -> str:
    """Load crew_manager.md from prompts dir. Returns minimal fallback if missing."""
    prompts_path = Path(__file__).resolve().parent.parent / "prompts" / "crew_manager.md"
    try:
        return prompts_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("manager prompt crew_manager.md not found — using minimal fallback")
        return (
            "You are a Crew Board Manager daemon. Decompose goals, assign agents, triage swimlanes, "
            "track progress, vet outputs, escalate stale items, and auto-close completed work. "
            "Always respond with structured JSON matching the BoardDecision format."
        )


class CrewBoardManager:
    """Autonomous board management daemon.

    Lifecycle: created at app startup, started via lifespan hook.
    Default state: enabled=False (OFF) until user toggles via /v1/crew/manager/toggle.
    Auto-disables on any uncaught exception during decision making.
    """

    MODEL_ID = "manager"
    POLL_INTERVAL_S = 60
    STALE_THRESHOLD_MIN = 30
    DECISION_TIMEOUT_S = 120
    TRIAGE_COOLDOWN_S = 300
    MAX_DECISIONS_PER_TICK = 3

    def __init__(
        self,
        store: CrewBoardStore,
        event_bus: Any,
        model_catalog: Any,
        *,
        poll_interval_s: float | None = None,
        ollama_invoker: Any | None = None,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._catalog = model_catalog
        self._ollama_model: str = ""
        self._poll_interval_s = poll_interval_s or self.POLL_INTERVAL_S

        self._stop = asyncio.Event()
        self._enabled = False
        self._model_ready = False
        self._decision_lock = asyncio.Lock()
        self._current_decision: BoardDecision | None = None
        self._bg_tasks: set[asyncio.Task] = set()
        self._last_triage = 0.0
        self._invoker = ollama_invoker

        self._decision_log: list[dict[str, Any]] = []
        self._max_log_entries = 50

        self._consecutive_errors = 0
        self._auto_disable_threshold = 3

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Background event loop. Runs until stop() is called."""
        if not self._catalog.is_available(self.MODEL_ID):
            log.warning("manager daemon: model %r unavailable — starting in disabled state", self.MODEL_ID)
            return

        entry = self._catalog.model(self.MODEL_ID)
        ollama_name = getattr(entry, "ollama_name", "") or ""
        if not ollama_name:
            log.warning("manager daemon: no ollama_name for model %r — cannot start", self.MODEL_ID)
            return

        self._ollama_model = ollama_name
        self._model_ready = True

        log.info("manager daemon: started (model=%s, poll=%.1fs)", self._ollama_model, self._poll_interval_s)

        while not self._stop.is_set():
            try:
                if self._enabled and self._model_ready:
                    await self._poll_tick()
                    self._consecutive_errors = 0
                else:
                    await asyncio.sleep(min(self._poll_interval_s, 5))

                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_s)
            except Exception:  # noqa: BLE001
                self._consecutive_errors += 1
                log.exception("manager daemon loop error (count=%d)", self._consecutive_errors)

                if self._consecutive_errors >= self._auto_disable_threshold:
                    log.critical("manager daemon: %d consecutive errors — auto-disabling", self._consecutive_errors)
                    self._enabled = False
                    break

    def stop(self) -> None:
        """Signal the event loop to exit and disable."""
        self._stop.set()
        self._enabled = False

    async def enable(self, model_name: str | None = None) -> bool:
        """Turn ON — returns True if enabled, False if model unavailable."""
        if not self._catalog.is_available(self.MODEL_ID):
            log.warning("manager daemon: cannot enable — model %r unavailable", self.MODEL_ID)
            return False

        if model_name:
            entry = self._catalog.model(model_name)
            ollama_name = getattr(entry, "ollama_name", "") or model_name
            self._ollama_model = ollama_name

        self._enabled = True
        self._model_ready = True
        self._consecutive_errors = 0
        log.info("manager daemon: enabled (model=%s)", self._ollama_model)
        return True

    def disable(self) -> None:
        """Turn OFF — daemon stays alive but takes no autonomous actions."""
        self._enabled = False
        self._consecutive_errors = 0
        log.info("manager daemon: disabled")

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "model_ready": self._model_ready,
            "model_id": self.MODEL_ID,
            "ollama_model": self._ollama_model,
            "current_decision": self._current_decision.action if self._current_decision else None,
            "decision_count": len(self._decision_log),
        }

    @property
    def activity(self) -> list[dict[str, Any]]:
        """Recent decisions (used by /activity endpoint)."""
        return list(self._decision_log[-self._max_log_entries:])

    # ── Board operations ───────────────────────────────────────────────

    async def decompose_goal(self, goal_text: str, project_slug: str) -> list[BoardDecision]:
        """Decompose a natural-language goal into dependency-chained tasks."""
        if not self._enabled or not self._model_ready:
            return []

        existing = self._store.list_tasks(project_slug=project_slug) if project_slug else []

        user_input = json.dumps({
            "type": "decompose_request",
            "goal": goal_text,
            "project": project_slug,
            "existing_projects": [t.project_slug for t in existing],
        })

        result = await self._make_decision("decompose", user_input)
        if not result:
            return []

        tasks = result.kwargs.get("tasks", [])
        decisions: list[BoardDecision] = []
        for i, task_data in enumerate(tasks):
            deps = [f"T-{i + 1}"] if i > 0 else []
            decisions.append(BoardDecision("create_task", **task_data, _order=i, _depends=deps))

        return decisions

    async def auto_assign(self, task_slug: str) -> BoardDecision | None:
        """Auto-assign an unassigned task to the best agent."""
        if not self._enabled or not self._model_ready:
            return None

        task = self._store.get_task(task_slug)
        if not task:
            log.warning("manager auto_assign: task %s not found", task_slug)
            return None

        assignees = self._store.count_tasks_by_status(status="in_progress")

        user_input = json.dumps({
            "type": "assign_request",
            "task": {"slug": task_slug, "title": task.title, "kind": task.kind},
            "current_assignee": task.assignee,
            "agent_load": assignees,
        })

        return await self._make_decision("assign", user_input)

    async def triage_board(self) -> list[BoardDecision]:
        """Full board triage: reorder swimlanes, promote stuck items."""
        if not self._enabled or not self._model_ready:
            return []

        now = time.monotonic()
        if now - self._last_triage < self.TRIAGE_COOLDOWN_S:
            return []

        ready = self._store.list_tasks(status=schema.STATUS_READY)
        proposed = self._store.list_tasks(status=schema.STATUS_PROPOSED)

        user_input = json.dumps({
            "type": "triage_request",
            "ready_count": len(ready),
            "proposed_count": len(proposed),
            "tasks": [
                {"slug": t.slug, "title": t.title, "priority": t.priority, "kind": t.kind}
                for t in (ready[:20] + proposed[:10])
            ],
        })

        result = await self._make_decision("triage", user_input)
        if result:
            self._last_triage = now
        return [result] if result else []

    async def vet_output(self, task_slug: str) -> BoardDecision | None:
        """Compare task verify_results against acceptance_criteria."""
        if not self._enabled or not self._model_ready:
            return None

        task = self._store.get_task(task_slug)
        if not task:
            return None

        user_input = json.dumps({
            "type": "vet_request",
            "task": {
                "slug": task.slug,
                "acceptance_criteria": task.acceptance_criteria,
                "verify_results": task.verify_results,
                "attempt_count": task.attempt_count,
            },
        })

        return await self._make_decision("vet", user_input)

    async def escalate_task(self, task_slug: str, reason: str = "") -> BoardDecision | None:
        """Escalate a stuck task to claude-code or human."""
        if not self._enabled or not self._model_ready:
            return None

        task = self._store.get_task(task_slug)
        if not task:
            return None

        user_input = json.dumps({
            "type": "escalate_request",
            "task": {"slug": task.slug, "title": task.title, "kind": task.kind},
            "attempts": task.attempt_count,
            "assignee_history": self._get_assignee_history(task_slug),
        })

        decision = await self._make_decision("escalate", user_input)
        if decision:
            escalation_target = decision.kwargs.get("to", "human")
            actor = "manager-daemon"
            if escalation_target == "claude-code":
                comment_body = f"Escalated to claude-code: {reason}"
            else:
                comment_body = f"Flagged for human review: {reason}"
            self._store.comment_task(task_slug, actor=actor, body=comment_body)
        return decision

    async def auto_close(self, task_slug: str) -> BoardDecision | None:
        """Auto-close when acceptance criteria met. Log lesson."""
        if not self._enabled or not self._model_ready:
            return None

        decision = await self.vet_output(task_slug)
        if not decision or not decision.kwargs.get("passed"):
            return None

        task = self._store.get_task(task_slug)
        if task:
            passed_criteria = [] if not decision.kwargs.get("missing_criteria") else decision.kwargs["missing_criteria"]
            lesson_body = (
                f"Lesson from {task_slug}: {'; '.join(str(c.get('text', '')) for c in task.acceptance_criteria)} "
                f"was satisfied on attempt {task.attempt_count}."
            )
            if hasattr(self._store, "record_lesson"):
                self._store.record_lesson(task.project_slug, task_slug, lesson_body, tags=["auto-close"])

        return decision

    # ── Internal helpers ───────────────────────────────────────────────

    async def _poll_tick(self) -> None:
        """Single poll iteration: check stale tasks, unassigned work, triage needs."""
        if self._decision_lock.locked():
            return

        decisions_made = 0
        max_decisions = self.MAX_DECISIONS_PER_TICK

        now = datetime.now(timezone.utc)

        # 1. Stale in_progress tasks
        try:
            stale_slugs = await self._find_stale_tasks(now)
            for slug in stale_slugs[:max_decisions - decisions_made]:
                if decisions_made >= max_decisions:
                    break
                dec = await self.escalate_task(slug, reason="stale (no action >30min)")
                if dec:
                    decisions_made += 1
                    self._record_decision(dec)
        except Exception:
            log.exception("manager stale check failed")

        # 2. Unassigned ready tasks
        if decisions_made >= max_decisions:
            return
        try:
            unassigned = await self._find_unassigned_tasks()
            for slug in unassigned[:max_decisions - decisions_made]:
                if decisions_made >= max_decisions:
                    break
                dec = await self.auto_assign(slug)
                if dec:
                    decisions_made += 1
                    self._record_decision(dec)
        except Exception:
            log.exception("manager unassigned check failed")

        # 3. Periodic triage (cooldown enforced inside triage_board)
        if decisions_made >= max_decisions:
            return
        try:
            triage_results = await self.triage_board()
            for dec in triage_results:
                if not isinstance(dec, BoardDecision):
                    continue
                entry = {"action": "triage", **dec.to_dict(), "timestamp": now.isoformat()}
                if len(self._decision_log) < self._max_log_entries:
                    self._decision_log.append(entry)
        except Exception:
            log.exception("manager triage check failed")

    async def _find_stale_tasks(self, now: datetime) -> list[str]:
        """Find in_progress tasks with no action for >STALE_THRESHOLD_MIN."""
        tasks = self._store.list_tasks(status=schema.STATUS_IN_PROGRESS)
        threshold = now - timedelta(minutes=self.STALE_THRESHOLD_MIN)
        stale: list[str] = []
        for task in tasks:
            last_action_str = getattr(task, "last_action", None)
            if not last_action_str:
                stale.append(task.slug)
                continue
            try:
                last_action = datetime.fromisoformat(last_action_str.replace("Z", "+00:00"))
                if last_action < threshold:
                    stale.append(task.slug)
            except (ValueError, AttributeError):
                stale.append(task.slug)
        return stale

    async def _find_unassigned_tasks(self) -> list[str]:
        """Find ready tasks with no assignee."""
        tasks = self._store.list_tasks(status=schema.STATUS_READY)
        return [t.slug for t in tasks if getattr(t, "assignee", "none") == "none"]

    def _get_assignee_history(self, task_slug: str) -> list[str]:
        """Get assignee change history from audit log."""
        try:
            audits = self._store.get_audit(task_slug)
            return [a.actor for a in audits if hasattr(a, "actor")]
        except Exception:
            return []

    async def _make_decision(self, action_hint: str, user_input: str) -> BoardDecision | None:
        """Send to Ollama and parse structured response. Single-flight guarded."""
        if not self._model_ready or not self._ollama_model:
            return None

        if self._decision_lock.locked():
            log.debug("manager: decision lock held for %s, skipping", action_hint)
            return None

        async with self._decision_lock:
            self._current_decision = BoardDecision(action_hint)

            try:
                text, tokens_in, tokens_out = await asyncio.wait_for(
                    self._invoke_ollama(user_input),
                    timeout=self.DECISION_TIMEOUT_S,
                )

                decision = self._parse_decision(text)
                if decision:
                    log.info("manager: decision %s (in=%d out=%d)", decision.action, tokens_in, tokens_out)
                    return decision

            except asyncio.TimeoutError:
                log.warning("manager: decision timeout for %s", action_hint)
            except Exception as e:
                log.error("manager: decision failed for %s: %s", action_hint, e)
            finally:
                self._current_decision = None

        return None

    async def _invoke_ollama(self, user_input: str) -> tuple[str, int, int]:
        """Call Ollama /api/chat with the current model."""
        if self._invoker:
            return await self._invoker.chat(
                model=self._ollama_model,
                system=user_input,
                params={"num_predict": 2048},
            )

        system_prompt = _load_system_prompt()
        body = {
            "model": self._ollama_model,
            "stream": False,
            "think": False,
            "keep_alive": "0",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "options": {"num_predict": 2048},
        }

        data = json.dumps(body).encode("utf-8")
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        req = urllib.request.Request(
            f"{host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=self.DECISION_TIMEOUT_S)
        result = json.loads(resp.read().decode("utf-8"))

        msg = result.get("message", {})
        text = msg.get("content", "")
        tokens_in = result.get("prompt_eval_count", 0)
        tokens_out = result.get("eval_count", 0)
        return (text, tokens_in, tokens_out)

    def _parse_decision(self, text: str) -> BoardDecision | None:
        """Parse the LLM's structured output using existing extract_json helper."""
        try:
            data = extract_json(text)
            if isinstance(data, dict):
                return BoardDecision.from_dict(data)
        except Exception:
            log.debug("manager: failed to parse decision from %s", text[:200])
        return None

    def _record_decision(self, decision: BoardDecision) -> None:
        """Record a decision in the activity log."""
        entry = {
            "action": decision.action,
            **decision.to_dict(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if len(self._decision_log) >= self._max_log_entries:
            self._decision_log.pop(0)
        self._decision_log.append(entry)


class BoardManagerEvent:
    """Event published to event_bus by the manager daemon."""

    def __init__(self, action: str, task_slug: str | None = None, **kwargs: Any) -> None:
        self.action = action
        self.task_slug = task_slug
        self.kwargs = kwargs
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "board_manager",
            "action": self.action,
            "task_slug": self.task_slug,
            **self.kwargs,
            "timestamp": self.timestamp,
        }


class BoardManagerError(Exception):
    """Raised when the daemon encounters an unrecoverable error."""
    pass
