"""Dispatcher — polls Ready tasks with a non-`none` assignee and
runs them through the right runner. Owns the escalation policy:
hive failures twice → promote to claude-code.

Loops every N seconds in the gateway lifespan. Single-flight: only
one task per assignee runs at a time so the LLM / claude pool isn't
saturated.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from gateway.crew_board import schema, worktree
from gateway.crew_board.store import CrewBoardStore
from gateway.crew_board.hive_runner import run_hive
from gateway.crew_board.hive_agent_loop import _DEFAULT_MODEL, run_hive_agent_loop
from gateway.crew_board.claude_runner import (
    distill_lesson, run_claude, run_claude_qa, run_claude_review,
)
from gateway.crew_board.verifier import verify
from gateway.crew_board.markdown_mirror import mirror_task, mirror_lessons

log = logging.getLogger("gateway.crew_board.dispatcher")

# Number of hive failures before escalating to the next rung.
ESCALATION_THRESHOLD = 2
# Base escalation ladder (2-rung default).  _build_ladder() extends it
# to 3-rung when crew_hive_lite_enabled=True.
_ESCALATION_LADDER_BASE = ["hive", "claude-code"]


def _build_ladder(*, hive_lite_enabled: bool = False) -> list[str]:
    """Build the active escalation ladder.

    Default (hive_lite_enabled=False): ``["hive", "claude-code"]``.
    Enabled:                            ``["hive", "hive-lite", "claude-code"]``.
    """
    if hive_lite_enabled:
        return ["hive", "hive-lite", "claude-code"]
    return list(_ESCALATION_LADDER_BASE)


# Module-level default ladder (2-rung).  The dispatcher instance replaces
# this with its own copy built from config.
_ESCALATION_LADDER = _build_ladder()


def _next_rung(assignee: str, ladder: list[str] | None = None) -> str | None:
    """Next worker in the escalation ladder, or None at the top."""
    _ladder = ladder if ladder is not None else _ESCALATION_LADDER
    try:
        i = _ladder.index(assignee)
    except ValueError:
        return None
    return _ladder[i + 1] if i + 1 < len(_ladder) else None


def _verdict_env_broken(verdict: object) -> bool:
    """True when verify failed because the configured ``test_cmd`` could
    not run *at all* — a spawn failure (Windows ``WinError 2`` on a
    ``.bat`` shim) or a missing project path — rather than because the
    code or tests were wrong.

    This is an environment fault, not an agent fault: retrying it just
    burns attempts and tokens (T-0301 wasted all 5 attempts + 144k
    claude tokens on a ``flutter test`` whose shim wouldn't resolve under
    the gateway's minimal PATH). The dispatcher parks such a task for the
    owner immediately. Mirrors the ``_spawn_failed`` predicate in
    ``verifier.verify`` so the two stay in lockstep.
    """
    tests = getattr(verdict, "tests", None) or {}
    reason = tests.get("reason") or ""
    return (not tests.get("ran")) and reason.startswith(
        ("could not spawn", "project path missing")
    )


# Hard cap: even claude-code stops retrying after this many attempts
# and the task is parked in `review` for the owner to look at. Prevents
# the runaway loops we saw on T-0001 (48 attempts).
MAX_TOTAL_ATTEMPTS = 5
# Anti-wedge: if there are runnable ready tasks but every one is blocked by an
# unmet dependency AND nothing is in_progress for this long, the board is
# silently wedged (a chain stuck behind a parked/review/missing task). We log +
# notify once so it surfaces on the dashboard instead of stalling unseen.
WEDGE_ALERT_S = 180.0
# Reviewer timeout (sec). If a task has been sitting in REVIEW with
# `review_by` set for this long without progress, we abandon the
# reviewer, comment the task, and push it back to READY so the next
# tick re-attempts. Was originally infinite, which caused tasks to
# stall forever when the reviewer subprocess hung silently.
REVIEW_TIMEOUT_S = 900  # 15 min
# QA timeout (sec). QA runs claude to write + run tests. If it stalls for
# this long the task is promoted directly to review (verify already passed
# earlier in the pipeline — the work is known-good, just un-QA'd).
QA_TIMEOUT_S = 900  # 15 min — matches reviewer; both are best-effort gates
# A task in_progress with no heartbeat for this long is a crash-orphan
# (a healthy hive run heartbeats every turn). Reaped back to ready.
# Longer than the slowest single turn, shorter than a wedged run.
STALE_INPROGRESS_S = 600  # 10 min
# How often the reaper sweeps the Done column for tasks older than the
# retention window (auto-archive). The sweep is cheap, but no point running it
# every 2s tick — once per 30 min keeps the column tidy without churn.
DONE_SWEEP_INTERVAL_S = 1800
# Max concurrent hive tasks per assignee on a `parallel=True` project.
# Each runs in its own git worktree, so there is no checkout collision.
# Default 1: the bench-proven model (qwen2.5-coder:7b-Q4) needs BOTH GPUs, so
# loading it twice would thrash. Parallel projects still benefit at 1
# lane via branch-per-task isolation (clean main checkout, per-tree
# rollback). Raise this ONLY after wiring a one-card model (Q3/IQ4) so
# two lanes can each hold a model — see the design doc.
# Runtime value is overridden by CrewDispatcher from config.crew_parallel_lane_cap.
PARALLEL_LANE_CAP = 1


class CrewDispatcher:
    def __init__(
        self,
        store: CrewBoardStore,
        coordinator,
        *,
        vault_path: Path | None = None,
        poll_interval_s: float = 5.0,
        notifier=None,
        daily_usd_cap: float | None = 20.0,
        # hive-lite plumbing: wired from config; default-off = no behaviour change.
        hive_lite_enabled: bool = False,
        hive_lite_model: str | None = None,
        parallel_lane_cap: int = 1,
        done_retention_days: float = 3.0,
        image_shim=None,
        video_shim=None,
        avatar_shim=None,
    ) -> None:
        self._store = store
        self._coordinator = coordinator
        # Content tasks (kind='content') are generated by these shims.
        self._image_shim = image_shim
        self._video_shim = video_shim
        self._avatar_shim = avatar_shim
        # Anti-wedge detector state (see WEDGE_ALERT_S).
        self._wedge_since: float | None = None
        self._wedge_notified: bool = False
        self._vault_path = vault_path
        self._poll_interval_s = poll_interval_s
        self._notifier = notifier
        # Rolling 24h claude escalation cost cap (USD). None or 0 = unlimited.
        self._daily_usd_cap: float | None = (
            daily_usd_cap if daily_usd_cap and daily_usd_cap > 0 else None
        )
        # hive-lite: build the per-instance ladder from config.
        self._escalation_ladder: list[str] = _build_ladder(
            hive_lite_enabled=hive_lite_enabled,
        )
        self._hive_lite_model: str | None = hive_lite_model
        # Per-instance lane cap (overrides module-level PARALLEL_LANE_CAP).
        self._parallel_lane_cap: int = max(1, int(parallel_lane_cap))
        # Auto-archive Done tasks older than this many days (0 = off).
        self._done_retention_days: float = done_retention_days
        self._last_done_sweep: float = 0.0
        self._stop = asyncio.Event()
        # Per-assignee single-flight locks.
        self._locks: dict[str, asyncio.Lock] = {}
        # Slugs this process is actively running — never reaped even if
        # a heartbeat momentarily lags (e.g. claude subprocess turn).
        self._running: set[str] = set()
        # Live lane count per assignee for parallel projects (incremented
        # at run start, decremented in the finally). Gates concurrency to
        # _parallel_lane_cap without serialising on the single-flight lock.
        self._lane_count: dict[str, int] = {}
        # Slugs claimed-but-not-yet-finished (added synchronously in
        # _tick BEFORE the async _run_task runs, removed in its finally).
        # Without this, two ticks within one task's lifetime can both
        # spawn _run_task for the same slug — the second then tries an
        # illegal in_progress->in_progress move. Keyed by slug for builds
        # and "review:<slug>" for reviews.
        self._inflight: set[str] = set()
        # Strong refs to fire-and-forget background tasks so they aren't
        # GC'd mid-flight and so their exceptions are surfaced, not
        # silently swallowed by the event loop.
        self._bg_tasks: set[asyncio.Task] = set()
        # Pause-state tracker: remember last observed value so we only log
        # the transition, not every tick.
        self._was_paused: bool = False

    def _lock(self, assignee: str) -> asyncio.Lock:
        if assignee not in self._locks:
            self._locks[assignee] = asyncio.Lock()
        return self._locks[assignee]

    def _spawn(self, coro) -> None:
        """Fire-and-forget a coroutine while keeping a strong ref and
        logging any exception (a bare create_task drops the ref and
        swallows exceptions)."""
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)

        def _done(fut: asyncio.Task) -> None:
            self._bg_tasks.discard(fut)
            if not fut.cancelled() and fut.exception() is not None:
                log.error("background task failed", exc_info=fut.exception())

        t.add_done_callback(_done)

    async def start(self) -> None:
        """Background loop. Runs until stop() is called."""
        log.info("crew dispatcher: starting (poll=%.1fs)", self._poll_interval_s)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("dispatcher tick failed")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._poll_interval_s,
                )
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        # Reaper FIRST: requeue crash-orphaned in_progress tasks. A live
        # runner heartbeats every turn (hive_agent_loop) so a healthy
        # long run is never reaped; a hung/dead one stops heartbeating
        # and gets bounced after STALE_INPROGRESS_S. This is what stops
        # the board accumulating 170 stuck in_progress tickets.
        self._reap_stale_in_progress()
        # Tidy the Done column: auto-archive done tasks past the retention
        # window. Self-throttled to DONE_SWEEP_INTERVAL_S internally.
        self._sweep_done_to_archive()
        # Pause gate: reaper already ran above (crash-orphan recovery keeps
        # working while paused). If paused, do NOT start any new dispatch or
        # review runs — log on first transition only to avoid log spam.
        if self._store.is_paused():
            if not self._was_paused:
                log.info("crew dispatcher: board paused — skipping dispatch")
                self._was_paused = True
            return
        if self._was_paused:
            log.info("crew dispatcher: board resumed — dispatch active")
            self._was_paused = False
        ready = self._store.list_tasks(status=schema.STATUS_READY)
        # Pre-compute the set of done slugs once per tick so depends_on
        # checks are O(1) instead of O(n) per task.
        done_slugs = self._store.done_slugs()
        # Track assignees we've already dispatched THIS tick. The
        # per-assignee lock is acquired async inside _run_task, so
        # without this set every ready task with the same assignee
        # would flip to in_progress in one tick before any lock is
        # held — they'd queue on the lock but all show in_progress
        # (and all stick on a crash). Claim at most one task per
        # assignee per tick.
        claimed_this_tick: set[str] = set()
        # Pending claims this tick for parallel assignees, so we don't
        # over-claim past the lane cap before _run_task increments.
        pending_lanes: dict[str, int] = {}
        # Per-tick project cache — avoids an N+1 get_project per ready
        # task (projects change rarely; a 5s-stale read is harmless).
        proj_cache: dict[str, object] = {}
        for task in ready:
            if task.assignee in ("none", "owner"):
                continue
            # Enforce depends_on: skip the task if any upstream isn't
            # done. The depends_on list is stored on Task as a JSON
            # array of task slugs (store.py:create_task). A task depending on
            # ITSELF (seen from a bad decompose) is ignored — otherwise it can
            # never be satisfied and silently wedges the whole chain behind it.
            unmet = [
                d for d in (task.depends_on or [])
                if d != task.slug and d not in done_slugs
            ]
            if unmet:
                continue
            # Already running from a prior tick — don't double-claim.
            if task.slug in self._inflight:
                continue
            if task.project_slug not in proj_cache:
                proj_cache[task.project_slug] = self._store.get_project(
                    task.project_slug
                )
            project = proj_cache[task.project_slug]
            is_parallel = bool(project is not None and project.parallel)
            if is_parallel:
                # Lane-gated: allow up to _parallel_lane_cap concurrent
                # tasks for this assignee (each in its own worktree).
                live = self._lane_count.get(task.assignee, 0)
                pend = pending_lanes.get(task.assignee, 0)
                if live + pend >= self._parallel_lane_cap:
                    continue
                pending_lanes[task.assignee] = pend + 1
                self._inflight.add(task.slug)
                self._spawn(self._run_task(task.slug))
                continue
            # Single-flight (default): one task per assignee per tick.
            if task.assignee in claimed_this_tick:
                continue
            lock = self._lock(task.assignee)
            if lock.locked():
                continue
            claimed_this_tick.add(task.assignee)
            self._inflight.add(task.slug)
            self._spawn(self._run_task(task.slug))

        # Anti-wedge: surface a board that has ready work but every task is
        # blocked by an unmet dependency with nothing running (silent stall).
        self._detect_wedge(ready, done_slugs)

        # Reviewer flow: any task sitting in REVIEW with review_by set
        # gets handed to a reviewer agent. The reviewer's verdict moves
        # the task to DONE (approve) or back to READY (reject).
        in_review = self._store.list_tasks(status=schema.STATUS_REVIEW)
        for task in in_review:
            review_by = getattr(task, "review_by", None)
            if not review_by:
                continue
            # Reviewer timeout: if the task has been stuck in REVIEW
            # too long the reviewer is hung. A task only REACHES review
            # after verify already passed (tests + smoke + files), so
            # the work is verified-good — just unreviewed. Auto-approve
            # to DONE rather than stalling forever.
            #
            # HOWEVER: "verify passed" means tests + files + commit gates
            # cleared — it does NOT mean any behavior was ever asserted.
            # We have historically shipped broken features (e.g. dashboard
            # panels that rendered nothing) because smoke_cmd was absent and
            # every test mocked the live system. The outcome_proven gate
            # closes this: auto-approve is only permitted when the latest
            # verify_results shows outcome_proven == True (a runnable
            # outcome probe — smoke_cmd — ran and exited 0). If no outcome
            # probe ran, we leave the task in REVIEW and notify David so he
            # can decide, rather than silently promoting a possibly-broken
            # feature to done.
            #
            # (Bug history: this used to try review->READY, which the
            # state machine forbids — review only goes to done/
            # in_progress/archived. The illegal move raised + silently
            # failed, so tasks hung in review indefinitely. That's the
            # 'reviewer-hang' we kept hitting.)
            if self._review_expired(task):
                # Read the latest verify_results to check outcome_proven.
                vr = getattr(task, "verify_results", None) or {}
                if isinstance(vr, str):
                    import json as _json
                    try:
                        vr = _json.loads(vr)
                    except Exception:
                        vr = {}
                # Strict identity check (is True), NOT bool(): this gate is
                # the false-done backstop, so it fails closed. A truthy
                # non-boolean (e.g. a hand-edited / corrupt DB storing the
                # string "false", where bool("false") == True) must NOT pass.
                # Only a genuine JSON boolean true — which is the only thing
                # verify() ever writes — proves the outcome.
                outcome_proven = vr.get("outcome_proven", False) is True
                outcome_reason = vr.get(
                    "outcome_reason", "no outcome probe recorded"
                )
                if outcome_proven:
                    # Behavior was asserted by a runnable probe — safe to
                    # auto-approve after the reviewer timeout.
                    self._store.add_comment(
                        task.slug, actor="system",
                        comment=(
                            f"reviewer ({review_by}) timed out after "
                            f"{REVIEW_TIMEOUT_S}s. verify passed AND "
                            f"outcome proven ({outcome_reason}), "
                            "auto-approving to done."
                        ),
                    )
                    self._store.move_task(
                        task.slug, schema.STATUS_DONE,
                        actor="system",
                        detail=f"auto-approved after review timeout "
                               f"({REVIEW_TIMEOUT_S}s); outcome proven",
                    )
                    self._notify("review_autoapproved", task.slug)
                    self._notify("review_timeout", task.slug)
                    # P6: a timeout-approved task is now done; check goal
                    # completion.
                    self._spawn(self._check_goal_completion(task.slug))
                    # #210: capture skill ideas from the (auto-approved) work.
                    self._spawn(self._suggest_skills_bg(task.slug))
                else:
                    # No outcome probe ran — we cannot assert that the
                    # feature actually works. Leave in REVIEW and surface
                    # to David instead of silently marking done.
                    self._store.add_comment(
                        task.slug, actor="system",
                        comment=(
                            f"reviewer ({review_by}) timed out after "
                            f"{REVIEW_TIMEOUT_S}s. NOT auto-approving: "
                            f"needs your review — no outcome probe ran "
                            f"({outcome_reason}). Tests passed but behavior "
                            "was never asserted. Add a smoke_cmd to this "
                            "task or project to enable auto-approve."
                        ),
                    )
                    self._notify("needs_review", task.slug)
                continue
            # Different lock namespace so reviewing doesn't block the
            # builder for the SAME assignee.
            if f"review:{task.slug}" in self._inflight:
                continue
            lock = self._lock(f"review:{review_by}")
            if lock.locked():
                continue
            self._inflight.add(f"review:{task.slug}")
            self._spawn(self._run_review(task.slug))
        # QA flow: tasks in STATUS_QA get claude to write automated tests
        # covering the acceptance criteria, then run the suite. Pass →
        # promote to review; fail → bounce back to ready (builder fixes).
        # The QA lock is per-agent ("qa:claude-code") — one QA run at a time
        # so we don't saturate the claude subprocess pool.
        in_qa = self._store.list_tasks(status=schema.STATUS_QA)
        for task in in_qa:
            # QA timeout: if the task has been sitting in QA too long the
            # subprocess is hung. Promote to review — verify already passed
            # (tests+smoke+files), so the work is safe to review without
            # new QA tests having been written.
            if self._qa_expired(task):
                self._store.add_comment(
                    task.slug, actor="system",
                    comment=(
                        f"QA timed out after {QA_TIMEOUT_S}s. verify already "
                        "passed (tests+smoke+files), promoting to review."
                    ),
                )
                self._store.set_review_by(task.slug, "claude-code")
                self._store.move_task(
                    task.slug, schema.STATUS_REVIEW,
                    actor="system",
                    detail="QA timed out; verify already passed, promoting to review",
                )
                self._notify("qa_timeout", task.slug)
                continue
            if f"qa:{task.slug}" in self._inflight:
                continue
            # One QA agent at a time — share the "qa:claude-code" lock so
            # parallel QA tasks serialise (same reasoning as reviewer lock).
            lock = self._lock("qa:claude-code")
            if lock.locked():
                continue
            self._inflight.add(f"qa:{task.slug}")
            self._spawn(self._run_qa(task.slug))

    def _git_head(self, project_path: str) -> str | None:
        """Current HEAD sha, or None if not a git repo.
        Sync — always call via asyncio.to_thread from async context."""
        import subprocess
        try:
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_path, capture_output=True,
                encoding="utf-8", errors="replace", timeout=10,
            )
            return (out.stdout or "").strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    def _git_hard_reset(self, project_path: str, sha: str) -> None:
        """Discard all working-tree changes back to *sha*. Used to undo
        a failed hive attempt that left broken code on disk poisoning
        every other task's tests.
        Sync — always call via asyncio.to_thread from async context."""
        import subprocess
        try:
            subprocess.run(
                ["git", "reset", "--hard", sha],
                cwd=project_path, capture_output=True,
                encoding="utf-8", errors="replace", timeout=30, check=False,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=project_path, capture_output=True,
                encoding="utf-8", errors="replace", timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            log.exception("git reset failed for %s", project_path)

    def _git_commit_all(self, project_path: str, message: str) -> None:
        """Stage + commit everything so a successful task is a clean
        restore point for the NEXT task's reset-on-failure.
        Sync — always call via asyncio.to_thread from async context."""
        import subprocess
        try:
            subprocess.run(
                ["git", "add", "-A"], cwd=project_path,
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=30, check=False,
            )
            subprocess.run(
                ["git", "commit", "-m", message, "--no-verify"],
                cwd=project_path, capture_output=True,
                encoding="utf-8", errors="replace", timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            log.exception("git commit failed for %s", project_path)

    def _git_push(self, project_path: str) -> None:
        """Push the project's current branch to origin. Best-effort —
        only called for projects with push_allowed=True. A push failure
        (offline gitea, auth) never blocks the pipeline.
        Sync — always call via asyncio.to_thread from async context."""
        import subprocess
        try:
            r = subprocess.run(
                ["git", "push", "origin", "HEAD"], cwd=project_path,
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=60, check=False,
            )
            if r.returncode != 0:
                log.warning("git push failed for %s: %s",
                            project_path, (r.stderr or "").strip()[:200])
            else:
                log.info("git push ok for %s", project_path)
        except (OSError, subprocess.SubprocessError):
            log.exception("git push errored for %s", project_path)

    def _detect_wedge(self, ready: list, done_slugs: set) -> None:
        """Surface a silently-wedged board. If every runnable ready task is
        blocked by an unmet dep and nothing is in_progress for WEDGE_ALERT_S,
        log + notify once with the blockers (and their statuses) so the wedge
        shows on the dashboard instead of stalling unseen. Self-clears the
        moment real work becomes eligible or starts running."""
        runnable = [t for t in ready if t.assignee not in ("none", "owner")]

        def _unmet(t):
            return [d for d in (t.depends_on or [])
                    if d != t.slug and d not in done_slugs]

        eligible = [t for t in runnable if not _unmet(t)]
        running = len(self._store.list_tasks(status=schema.STATUS_IN_PROGRESS)) \
            + len(self._running)

        if not runnable or eligible or running:
            # Work is available, flowing, or there's simply nothing to run —
            # not a wedge. Clear the latch.
            self._wedge_since = None
            self._wedge_notified = False
            return

        now = time.monotonic()
        if self._wedge_since is None:
            self._wedge_since = now
            return
        if (now - self._wedge_since) < WEDGE_ALERT_S or self._wedge_notified:
            return

        # Wedged long enough — identify + surface the blockers.
        all_tasks = {t.slug: t for t in self._store.list_tasks()}
        blockers: dict[str, str] = {}
        for t in runnable:
            for d in _unmet(t):
                bt = all_tasks.get(d)
                blockers[d] = bt.status if bt is not None else "missing"
        log.warning(
            "BOARD WEDGED: %d ready tasks blocked, 0 running. Blockers (slug→"
            "status): %s — these need owner attention (parked/review/missing "
            "deps will never auto-complete).", len(runnable), blockers,
        )
        self._notify("board_wedged", "")
        if self._notifier is not None:
            try:
                self._notifier.broadcast({
                    "event": "board_wedged",
                    "ready_blocked": len(runnable),
                    "blockers": blockers,
                })
            except Exception:  # noqa: BLE001
                pass
        self._wedge_notified = True

    async def _run_content(self, task) -> None:
        """Generate a content task's media via the Image/Video shim, store the
        result media ids on content_spec, and land it in `done`. No verify/QA/
        review — the media is the deliverable. On failure, park in `review`.
        """
        slug = task.slug
        spec = dict(task.content_spec or {})
        ctype = str(spec.get("type", "image")).lower()
        prompt = str(spec.get("prompt", "")).strip()
        try:
            if not prompt:
                raise ValueError("content request has no prompt")
            if ctype == "video":
                if self._video_shim is None:
                    raise RuntimeError("video shim not configured")
                seed = spec.get("seed_media_id")
                if not seed:
                    raise ValueError("video request needs a seed_media_id")
                job = await self._video_shim.enqueue(
                    prompt=prompt, seed_image_media_id=str(seed),
                    negative_prompt=str(spec.get("negative_prompt", "")),
                )
                getter = getattr(self._video_shim, "get", None)
                max_polls = 180   # ~6 min at 2s
            elif ctype == "avatar":
                if self._avatar_shim is None:
                    raise RuntimeError("avatar shim not configured")
                # `prompt` is the spoken script. An optional face image is a
                # previously-generated image media id, resolved to a path.
                image_path = None
                img_mid = spec.get("image_media_id")
                if img_mid and self._image_shim is not None:
                    p = self._image_shim.media_path(str(img_mid))
                    image_path = str(p) if p else None
                job = await self._avatar_shim.enqueue(
                    script=prompt,
                    image_path=image_path,
                    avatar_name=str(spec.get("avatar_name", "ai_woman")),
                    voice=str(spec.get("voice", "af_heart")),
                    preprocess=str(spec.get("preprocess", "crop")),
                    still=bool(spec.get("still", False)),
                )
                getter = getattr(self._avatar_shim, "get", None)
                max_polls = 360   # ~12 min at 2s (SadTalker is slow)
            else:
                if self._image_shim is None:
                    raise RuntimeError("image shim not configured")
                job = await self._image_shim.enqueue(
                    prompt=prompt, count=int(spec.get("count", 1)),
                    model=spec.get("model"),
                    width=int(spec.get("width", 1024)),
                    height=int(spec.get("height", 1024)),
                    steps=int(spec.get("steps", 20)),
                    guidance=float(spec.get("guidance", 3.5)),
                    negative_prompt=str(spec.get("negative_prompt", "")),
                    seed=int(spec.get("seed", -1)),
                    enhance=bool(spec.get("enhance", True)),
                )
                getter = getattr(self._image_shim, "get", None)
                max_polls = 120   # ~4 min at 2s

            self._store.set_last_action(slug, f"generating {ctype}…")
            # Poll the job to completion (shim runs it on a background thread).
            for _ in range(max_polls):
                if self._stop.is_set():
                    break
                cur = getter(job.id) if getter else None
                if cur is not None and cur.state in ("done", "error"):
                    job = cur
                    break
                await asyncio.sleep(2.0)

            media = list(getattr(job, "result_ids", []) or [])
            spec.update(job_id=job.id, state=job.state, result_media_ids=media)
            self._store.set_content_spec(slug, spec)

            if job.state == "done" and media:
                # in_progress -> review -> done (state machine has no direct
                # in_progress->done; content needs no human approval).
                self._store.move_task(slug, schema.STATUS_REVIEW,
                                      actor="content", detail="generated")
                self._store.move_task(slug, schema.STATUS_DONE,
                                      actor="content",
                                      detail=f"{len(media)} media generated")
                self._notify("content_done", slug)
            else:
                self._store.move_task(
                    slug, schema.STATUS_REVIEW, actor="content",
                    detail=f"generation {job.state or 'failed'} — owner review",
                )
        except Exception as e:  # noqa: BLE001
            log.warning("content task %s failed: %s", slug, e)
            spec.update(state="error", error=str(e)[:200])
            try:
                self._store.set_content_spec(slug, spec)
                self._store.move_task(slug, schema.STATUS_REVIEW, actor="content",
                                      detail=f"content error: {e}")
            except Exception:  # noqa: BLE001
                pass

    async def _run_task(self, slug: str) -> None:
        # H1/H2: fetch the task and bail early if it vanished or was
        # already moved out of ready (race with reaper or route handler)
        # BEFORE we do anything that would need cleanup.
        task = self._store.get_task(slug)
        if task is None:
            self._inflight.discard(slug)
            return
        # Re-check status under the inflight guard: if a route handler or
        # the reaper moved the task out of ready between _tick's check and
        # now, bail cleanly rather than attempting an illegal transition.
        if task.status != schema.STATUS_READY:
            self._inflight.discard(slug)
            return
        base_project = self._store.get_project(task.project_slug)
        is_parallel = bool(base_project is not None and base_project.parallel)
        # Parallel projects run each task in its own worktree, so they
        # lock per-SLUG (no cross-task serialisation). Single-flight
        # projects lock per-ASSIGNEE as before.
        lock = self._lock(f"task:{slug}" if is_parallel else task.assignee)
        async with lock:
            # H1/H2: re-fetch inside the lock so we see any move that
            # happened between the pre-lock check and acquiring the lock.
            task = self._store.get_task(slug)
            if task is None or task.status != schema.STATUS_READY:
                self._inflight.discard(slug)
                return
            # HARD ATTEMPT CAP (single chokepoint). Every re-run goes through
            # this claim, so refusing to claim a task already at the cap stops
            # ALL runaway loops regardless of which path re-queued it (review
            # reject, review timeout, qa fail, verifier reject). Observed:
            # T-0316 (claude-code) burned 340 turns / 13 attempts because the
            # reviewer kept rejecting review->ready and no bounce path checked
            # the cap. Park in backlog (valid ready->backlog) for owner triage.
            if task.attempt_count >= MAX_TOTAL_ATTEMPTS:
                try:
                    self._store.move_task(
                        slug, schema.STATUS_BACKLOG, actor="system",
                        detail=(f"max attempts ({MAX_TOTAL_ATTEMPTS}) hit — "
                                "parked in backlog for owner; not re-running"),
                    )
                    self._notify("max_attempts", slug)
                    self._store.add_comment(
                        slug, "system",
                        f"Auto-parked: {task.attempt_count} attempts >= cap "
                        f"{MAX_TOTAL_ATTEMPTS}. The pipeline could not verify/"
                        "approve this task; needs owner review (likely an "
                        "unverifiable test_cmd or unsatisfiable criteria).",
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("cap-park failed for %s: %s", slug, e)
                self._inflight.discard(slug)
                return
            # Claim: move ready -> in_progress. Wrap in try/except so a
            # concurrent state change (ValueError from state machine)
            # causes a clean bail instead of leaking the inflight entry.
            try:
                self._store.move_task(
                    slug, schema.STATUS_IN_PROGRESS,
                    actor=task.assignee, detail="dispatcher claim",
                )
            except ValueError:
                # Another actor changed state between our check and the move.
                self._inflight.discard(slug)
                return
            self._store.increment_attempt(slug)
            task = self._store.get_task(slug)
            if task is None:
                self._inflight.discard(slug)
                return
            # `project` is the working tree the runners + verifier + git
            # ops operate on. For parallel projects it's an isolated git
            # worktree on branch crew/<slug>; otherwise the shared
            # checkout. Falls back to the shared checkout if worktree
            # creation fails (best-effort isolation).
            project = base_project
            if is_parallel and base_project is not None:
                # C1: worktree.ensure_worktree calls blocking subprocess.run —
                # offload to a thread so the event loop stays responsive.
                project = await asyncio.to_thread(
                    self._make_worktree_project, base_project, slug
                )
            # C1: _git_head is a blocking subprocess.run — offload to thread.
            pre_sha = (
                await asyncio.to_thread(self._git_head, project.path)
                if project is not None else None
            )
            if is_parallel:
                self._lane_count[task.assignee] = (
                    self._lane_count.get(task.assignee, 0) + 1
                )
            self._running.add(slug)
            try:
                if task.kind == "content":
                    # Content request — generate via the Image/Video shim
                    # instead of a code runner. No verify/QA/review: the media
                    # IS the deliverable (lands in `done`, shown in the gallery).
                    await self._run_content(task)
                    return
                # "What was accomplished" shown on the ticket when David opens
                # it — set richer per-assignee below; default for non-hive paths.
                done_detail = "agent finished; awaiting QA"
                # #198: who wrote done_detail (the model/agent that last worked
                # this ticket) — stamped onto the task as the handoff summary.
                _summary_by = task.assignee
                if task.assignee in {"hive", "hive-1", "hive-2", "hive-lite"}:
                    # Use the multi-turn agentic loop (hive_agent_loop)
                    # that the v5 bench validated. The old `run_hive`
                    # from hive_runner.py is plan-only and inadequate
                    # for real code work.
                    #
                    # hive-lite: same loop, but uses the configured
                    # lightweight model (crew_hive_lite_model) so it
                    # fits in a single GPU lane.  Falls back to the
                    # default _DEFAULT_MODEL when none is configured.
                    polish = getattr(task, "polish_iters", None) or 2
                    transcript_path = None
                    if self._vault_path is not None:
                        transcript_path = (
                            self._vault_path
                            / ".crew_transcripts"
                            / f"{slug}.json"
                        )
                        # Ensure parent dir exists — hive_agent_loop's
                        # _flush_transcript bombs on a missing dir and
                        # spams the log every turn.
                        try:
                            transcript_path.parent.mkdir(
                                parents=True, exist_ok=True,
                            )
                        except OSError:
                            log.exception(
                                "could not create transcript dir %s",
                                transcript_path.parent,
                            )
                            transcript_path = None
                    # Select model: hive-lite gets its own configured model;
                    # regular hive assignees use the loop's _DEFAULT_MODEL.
                    loop_kwargs: dict = {}
                    if task.assignee == "hive-lite" and self._hive_lite_model:
                        loop_kwargs["model"] = self._hive_lite_model
                    # Per-lane (board column) model override. The in_progress lane
                    # IS the build model, so its override drives the hive loop.
                    # Set from the board column header; empty/unset = default.
                    try:
                        _lane_model = self._store.get_meta("lane_model:in_progress")
                        if _lane_model and _lane_model.strip():
                            loop_kwargs["model"] = _lane_model.strip()
                            log.info(
                                "crew: in_progress lane model override -> %s (task %s)",
                                _lane_model.strip(), slug,
                            )
                    except Exception:  # noqa: BLE001
                        pass
                    loop_result = await run_hive_agent_loop(
                        self._store, task,
                        project=project,
                        max_iters=200,
                        transcript_path=transcript_path,
                        consecutive_greens_to_auto_done=polish,
                        notifier=self._notifier,
                        vault_path=self._vault_path,
                        **loop_kwargs,
                    )
                    ok = loop_result.ok
                    done_detail = (
                        loop_result.summary.strip()
                        or loop_result.reason.strip()
                        or done_detail
                    )
                    _summary_by = loop_kwargs.get("model") or _DEFAULT_MODEL
                    self._store.add_comment(
                        slug, actor=task.assignee,
                        comment=(
                            f"hive-loop finished: ok={ok} "
                            f"turns={loop_result.turns} "
                            f"reason={loop_result.reason!r}\n"
                            f"{loop_result.summary.strip()}"
                        ),
                    )
                elif task.assignee == "hive-legacy":
                    # Original one-shot HiveCoordinator path retained
                    # for chat-style planning tasks. Not used by SC build.
                    result = await run_hive(
                        self._store, task,
                        coordinator=self._coordinator,
                    )
                    ok = result.ok
                    done_detail = (result.reason or "hive (legacy) finished").strip()
                    _summary_by = "hive-legacy"
                    self._store.add_comment(
                        slug, actor="hive-legacy",
                        comment=(
                            f"hive (legacy plan-only) finished: ok={ok} "
                            f"actions={result.actions_attempted} "
                            f"reason={result.reason!r}"
                        ),
                    )
                elif task.assignee == "claude-code":
                    cr = await run_claude(self._store, task, project=project)
                    ok = cr.ok
                    if getattr(cr, "tokens", 0):
                        self._store.add_tokens(
                            slug, kind="claude", n=cr.tokens,
                        )
                    self._store.add_comment(
                        slug, actor="claude-code",
                        comment=(
                            f"claude run finished: ok={ok} "
                            f"exit={cr.exit_code} duration={cr.duration_s:.1f}s "
                            f"tokens={getattr(cr, 'tokens', 0)} "
                            f"reason={cr.reason!r}"
                        ),
                    )
                    done_detail = (cr.reason or "claude run finished").strip()
                    _summary_by = "claude-code"
                    self._store.update_verify_results(slug, {
                        "claude_stdout_tail": cr.stdout_tail,
                        "claude_stderr_tail": cr.stderr_tail,
                        "claude_exit": cr.exit_code,
                    })
                else:
                    self._store.add_comment(
                        slug, actor="system",
                        comment=f"unknown assignee {task.assignee!r}; back to ready",
                    )
                    self._store.move_task(
                        slug, schema.STATUS_READY,
                        actor="system", detail="unknown assignee",
                    )
                    return

                # #198: record the last-agent handoff summary on the ticket so
                # opening it shows what the most recent worker did + where it
                # left off, without reading the transcript. Overwrites each run.
                try:
                    self._store.set_task_summary(slug, done_detail, by=_summary_by)
                except Exception:  # noqa: BLE001
                    log.debug("set_task_summary failed for %s", slug, exc_info=True)

                # C1: verify() calls blocking subprocess.run (pytest up to
                # 180s + smoke 120s) — offload to a thread so the event
                # loop stays responsive. CRITICAL: do NOT pass store calls
                # into the thread; verify only reads task/project attrs
                # (already passed as values) but also calls
                # store.update_verify_results — that's fine because the
                # store lock (RLock) serialises it safely.
                _task_snap = self._store.get_task(slug)
                _proj_snap = project
                verdict = await asyncio.to_thread(
                    lambda: verify(
                        self._store, _task_snap,  # type: ignore[arg-type]
                        project=_proj_snap,
                    )
                )
                if ok and verdict.ok:
                    # Success: commit the project so this is a clean
                    # restore point for the NEXT task's rollback.
                    if project is not None:
                        # C1: blocking git subprocess — offload to thread.
                        await asyncio.to_thread(
                            self._git_commit_all,
                            project.path,
                            f"{slug}: {task.title[:60]} (hive verified)",
                        )
                        # Push to origin (gitea) when the project opts in,
                        # so verified work lands on the remote, not just
                        # the local checkout. Best-effort, never blocks.
                        if getattr(base_project or project, "push_allowed",
                                   False):
                            # C1: blocking git subprocess — offload to thread.
                            await asyncio.to_thread(
                                self._git_push,
                                (base_project or project).path,
                            )
                    # H5: in parallel mode the commit landed only on the
                    # task's crew/<slug> branch in its worktree. Merge it
                    # back into the base branch so the verified work is
                    # visible to the NEXT task (which branches off base).
                    if is_parallel and base_project is not None:
                        # C1: blocking git subprocess — offload to thread.
                        merged = await asyncio.to_thread(
                            worktree.merge_into_base, base_project.path, slug
                        )
                        if not merged:
                            self._store.add_comment(
                                slug, actor="system",
                                comment=(
                                    f"WARNING: could not auto-merge "
                                    f"crew/{slug.lower()} into base "
                                    "(conflict?) — branch + worktree kept "
                                    "for manual merge."
                                ),
                            )
                    # Land in QA — claude will write automated tests covering
                    # the acceptance criteria, run them, then promote to
                    # review (pass) or bounce to ready (fail). Do NOT block
                    # the state move on the claude lesson subprocess (H1).
                    self._store.move_task(
                        slug, schema.STATUS_QA,
                        actor=task.assignee,
                        detail=done_detail,
                    )
                    self._notify("qa_ready", slug)
                    # P5: distill a cross-task lesson from a claude rescue
                    # AFTER the move + detached, so the (up-to-180s) extra
                    # subprocess never holds the lane or stalls the move.
                    if task.assignee == "claude-code":
                        self._spawn(self._distill_and_comment(slug))
                else:
                    # Failure: roll the working tree back to the
                    # pre-attempt HEAD so broken code doesn't poison
                    # the next task's full-suite test run. This is the
                    # key fix from the SC-improvement postmortem where
                    # a failed minimap attempt left dict-shaped code on
                    # disk that broke every other task's smoke gate.
                    if project is not None and pre_sha is not None:
                        # C1: blocking git subprocess — offload to thread.
                        await asyncio.to_thread(
                            self._git_hard_reset, project.path, pre_sha
                        )
                        self._store.add_comment(
                            slug, actor="system",
                            comment=(
                                f"rolled back working tree to {pre_sha[:8]} "
                                "after failed attempt"
                            ),
                        )
                    elif project is not None and pre_sha is None:
                        # H6: no pre-attempt sha (not a git repo, or git
                        # errored) means we CAN'T roll back — broken code
                        # may stay on disk and poison the next task. Surface
                        # it instead of failing silently.
                        log.warning(
                            "no pre_sha for %s — cannot roll back failed "
                            "attempt; broken files may persist", slug,
                        )
                        self._store.add_comment(
                            slug, actor="system",
                            comment=(
                                "WARNING: no git restore point — failed "
                                "attempt could NOT be rolled back."
                            ),
                        )
                    refreshed = self._store.get_task(slug)
                    if refreshed is None:
                        return
                    # Broken test ENVIRONMENT (test_cmd can't spawn / project
                    # path missing) is not the agent's fault — every retry
                    # fails the gate identically, so retrying only burns
                    # attempts and tokens (T-0301 lost all 5 attempts + 144k
                    # claude tokens this way). Park for the owner immediately,
                    # before the escalation ladder promotes it to the paid rung.
                    if _verdict_env_broken(verdict):
                        self._store.add_comment(
                            slug, actor="system",
                            comment=(
                                "broken test environment — parking for owner "
                                "without consuming further attempts (retrying "
                                "an unspawnable test_cmd never helps). "
                                f"verdict: {verdict.reason}"
                            ),
                        )
                        self._store.move_task(
                            slug, schema.STATUS_REVIEW,
                            actor="system",
                            detail="broken test environment; owner review",
                        )
                        self._notify("env_broken", slug)
                        return
                    # Hard cap: park in review with a failure label.
                    if refreshed.attempt_count >= MAX_TOTAL_ATTEMPTS:
                        self._store.add_comment(
                            slug, actor="system",
                            comment=(
                                f"max attempts ({MAX_TOTAL_ATTEMPTS}) hit; "
                                f"parking in review for owner. last verdict: "
                                f"{verdict.reason!r}"
                            ),
                        )
                        self._store.move_task(
                            slug, schema.STATUS_REVIEW,
                            actor="system",
                            detail="max attempts; owner review",
                        )
                        self._notify("max_attempts", slug)
                    elif (
                        _next_rung(refreshed.assignee, self._escalation_ladder) is not None
                        and refreshed.attempt_count >= ESCALATION_THRESHOLD
                    ):
                        nxt = _next_rung(refreshed.assignee, self._escalation_ladder)
                        # Cost cap: check rolling 24h claude spend before
                        # promoting to claude-code (the paid rung).
                        if nxt == "claude-code" and self._daily_usd_cap is not None:
                            spent = self._store.rolling_24h_claude_cost_usd()
                            if spent >= self._daily_usd_cap:
                                log.warning(
                                    "escalation blocked for %s: daily cap $%.2f "
                                    "exceeded (spent $%.2f in last 24h)",
                                    slug, self._daily_usd_cap, spent,
                                )
                                self._store.add_comment(
                                    slug, actor="system",
                                    comment=(
                                        f"daily escalation budget "
                                        f"(${self._daily_usd_cap:.2f}) exceeded "
                                        f"(spent ${spent:.2f} in last 24h); "
                                        "parking for owner"
                                    ),
                                )
                                self._store.move_task(
                                    slug, schema.STATUS_REVIEW,
                                    actor="system",
                                    detail="daily escalation budget exceeded",
                                )
                                self._notify("escalation_budget_exceeded", slug)
                                return
                        self._store.assign_task(slug, nxt, actor="system")
                        self._store.add_comment(
                            slug, actor="system",
                            comment=(
                                f"escalated {refreshed.assignee} -> {nxt} after "
                                f"{refreshed.attempt_count} attempts"
                            ),
                        )
                        # Back to ready so the next tick picks it up.
                        self._store.move_task(
                            slug, schema.STATUS_READY,
                            actor="system", detail="escalation",
                        )
                        self._notify("escalated", slug)
                    else:
                        self._store.move_task(
                            slug, schema.STATUS_READY,
                            actor=task.assignee,
                            detail=f"verifier rejected: {verdict.reason}",
                        )
            finally:
                self._running.discard(slug)
                self._inflight.discard(slug)
                if is_parallel:
                    # Release the lane. Remove the worktree only when the
                    # task has LEFT in_progress (success → review, or
                    # parked/escalated); keep it if it's still mid-flight
                    # (shouldn't happen under the lock, but be safe). The
                    # crew/<slug> branch is left in place for merge.
                    self._lane_count[task.assignee] = max(
                        0, self._lane_count.get(task.assignee, 1) - 1
                    )
                    final = self._store.get_task(slug)
                    if (
                        base_project is not None
                        and (final is None
                             or final.status != schema.STATUS_IN_PROGRESS)
                    ):
                        try:
                            # C1: blocking git subprocess — offload to thread.
                            await asyncio.to_thread(
                                worktree.remove_worktree,
                                base_project.path, slug,
                            )
                        except Exception:  # noqa: BLE001
                            log.exception("worktree cleanup failed for %s", slug)
                # Mirror at every step boundary so the markdown view
                # stays current.
                if self._vault_path is not None:
                    refreshed = self._store.get_task(slug)
                    if refreshed is not None:
                        try:
                            mirror_task(refreshed, self._vault_path)
                        except Exception:  # noqa: BLE001
                            log.exception("mirror failed for %s", slug)

    def _make_worktree_project(self, base_project, slug: str):
        """Return a Project clone whose `path` points at an isolated git
        worktree for `slug` (branch crew/<slug>). Falls back to the
        shared checkout if worktree creation fails."""
        import dataclasses
        try:
            wt = worktree.ensure_worktree(base_project.path, slug)
        except worktree.WorktreeError:
            log.exception(
                "worktree create failed for %s; using shared checkout", slug
            )
            return base_project
        return dataclasses.replace(base_project, path=str(wt))

    async def _distill_and_comment(self, slug: str) -> None:
        """Detached: distill a lesson from a claude rescue and comment it.
        Runs after the task already moved to REVIEW so it never holds a
        lock/lane (the claude subprocess can take up to ~180s)."""
        task = self._store.get_task(slug)
        if task is None:
            return
        lesson = await distill_lesson(self._store, task)
        if lesson:
            self._store.add_comment(
                slug, actor="claude-code",
                comment=f"lesson recorded: {lesson[:200]}",
            )
            # Mirror the project's lessons into the Obsidian vault so the
            # hive's knowledge lives in notes, not just the DB.
            if self._vault_path is not None:
                try:
                    mirror_lessons(
                        self._store, task.project_slug, self._vault_path,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("lesson mirror failed for %s",
                                  task.project_slug)

    def _sweep_done_to_archive(self) -> None:
        """Auto-archive Done tasks older than the retention window so the Done
        column doesn't grow without bound. Self-throttled to one sweep per
        DONE_SWEEP_INTERVAL_S; disabled when done_retention_days <= 0."""
        if self._done_retention_days <= 0:
            return
        now = time.monotonic()
        if now - self._last_done_sweep < DONE_SWEEP_INTERVAL_S:
            return
        self._last_done_sweep = now
        try:
            n = self._store.archive_old_done(self._done_retention_days)
        except Exception:  # noqa: BLE001
            log.exception("done-sweep failed")
            return
        if n > 0:
            log.info(
                "done-sweep: archived %d done task(s) older than %.1f day(s)",
                n, self._done_retention_days,
            )
            if self._notifier is not None:
                try:
                    self._notifier.broadcast({"event": "done_swept", "count": n})
                except Exception:  # noqa: BLE001
                    pass

    def _reap_stale_in_progress(self) -> None:
        """Requeue in_progress tasks that this process is NOT running
        and whose heartbeat (or updated_at fallback) is older than
        STALE_INPROGRESS_S. Prevents the board accumulating stuck
        in_progress tickets from crashed/killed drivers."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for task in self._store.list_tasks(status=schema.STATUS_IN_PROGRESS):
            if task.slug in self._running:
                continue
            # M5: a slug in _inflight has been claimed but _running.add
            # hasn't happened yet (the task is between the claim move and
            # the self._running.add a few lines later in _run_task).
            # Skip it so the reaper doesn't race the live claim.
            if task.slug in self._inflight:
                continue
            stamp = getattr(task, "heartbeat_at", None) or task.updated_at
            if not stamp:
                continue
            try:
                dt = datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            if (now - dt).total_seconds() < STALE_INPROGRESS_S:
                continue
            # Orphan: bounce to ready. Pre-escalate a stuck hive task.
            try:
                self._store.add_comment(
                    task.slug, actor="system",
                    comment=(
                        f"reaper: in_progress with no heartbeat for "
                        f">{STALE_INPROGRESS_S}s; requeuing to ready"
                    ),
                )
                self._store.move_task(
                    task.slug, schema.STATUS_READY, actor="system",
                    detail="reaped stale in_progress",
                )
                if task.assignee == "hive" and task.attempt_count >= ESCALATION_THRESHOLD:
                    self._store.assign_task(
                        task.slug, "claude-code", actor="system",
                    )
                self._notify("reaped", task.slug)
            except ValueError:
                pass

    def _status_age_exceeded(self, task, timeout_s: float) -> bool:
        """True if task.updated_at is older than timeout_s seconds.
        Shared by both _review_expired and _qa_expired so the datetime
        parse logic lives in one place."""
        ts = getattr(task, "updated_at", "") or ""
        if not ts:
            return False
        try:
            from datetime import datetime, timezone
            # SQLite datetime('now') format: 'YYYY-MM-DD HH:MM:SS' (UTC)
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            return age > timeout_s
        except (ValueError, TypeError):
            return False

    def _review_expired(self, task) -> bool:
        """True if the task has been sitting in REVIEW longer than
        REVIEW_TIMEOUT_S. Uses Task.updated_at as the freshness clock."""
        return self._status_age_exceeded(task, REVIEW_TIMEOUT_S)

    def _qa_expired(self, task) -> bool:
        """True if the task has been sitting in QA longer than
        QA_TIMEOUT_S. Uses Task.updated_at as the freshness clock."""
        return self._status_age_exceeded(task, QA_TIMEOUT_S)

    async def _run_qa(self, slug: str) -> None:
        """Dispatch a task to claude for QA test writing + run. Pass →
        qa→review; fail → qa→ready (builder must fix failing tests)."""
        try:
            await self._run_qa_body(slug)
        finally:
            self._inflight.discard(f"qa:{slug}")

    async def _run_qa_body(self, slug: str) -> None:
        task = self._store.get_task(slug)
        if task is None:
            return
        project = self._store.get_project(task.project_slug)
        # QA runs on the shared checkout — parallel-worktree tasks already
        # merged their branch into base before reaching QA.
        lock = self._lock("qa:claude-code")
        async with lock:
            try:
                verdict = await run_claude_qa(self._store, task, project)
            except Exception as e:  # noqa: BLE001
                log.exception("qa runner crashed for %s", slug)
                self._store.add_comment(
                    slug, actor="system",
                    comment=f"QA runner crashed: {type(e).__name__}: {e}",
                )
                # Leave in qa — the timeout path will recover after QA_TIMEOUT_S
                return
            tests_note = (
                f" (tests added: {', '.join(verdict.tests_added)})"
                if verdict.tests_added else ""
            )
            self._store.add_comment(
                slug, actor="claude-code",
                comment=(
                    f"QA verdict: {'PASS' if verdict.passed else 'FAIL'}"
                    f" — {verdict.reason}{tests_note}"
                ),
            )
            if verdict.passed:
                # Commit the new/updated test files so they are part of the
                # project's permanent test suite (not just an ephemeral run).
                # C1: blocking git subprocess — offload to thread.
                if project is not None:
                    await asyncio.to_thread(
                        self._git_commit_all,
                        project.path, f"{slug}: QA tests added",
                    )
                # Set the reviewer BEFORE the move so the review loop
                # (which skips review_by=None) actually picks it up — else
                # QA-passed work strands in review forever (observed: T-0307).
                self._store.set_review_by(slug, "claude-code")
                self._store.move_task(
                    slug, schema.STATUS_REVIEW, actor="claude-code",
                    detail="QA passed; tests committed",
                )
                self._notify("qa_passed", slug)
            else:
                # QA failed — send back to ready so the builder can fix the
                # code (or tests, if they expose a real defect).
                self._store.move_task(
                    slug, schema.STATUS_READY, actor="claude-code",
                    detail=f"QA failed: {verdict.reason}",
                )
                self._notify("qa_failed", slug)

    async def _run_review(self, slug: str) -> None:
        """Dispatch a task to its reviewer agent (claude-code) for an
        approve/reject verdict. Approve -> DONE; reject -> READY with
        the verdict comment + back to the original assignee."""
        try:
            await self._run_review_body(slug)
        finally:
            self._inflight.discard(f"review:{slug}")

    async def _run_review_body(self, slug: str) -> None:
        task = self._store.get_task(slug)
        if task is None:
            return
        review_by = getattr(task, "review_by", None) or "claude-code"
        lock = self._lock(f"review:{review_by}")
        async with lock:
            try:
                verdict = await run_claude_review(self._store, task)
            except Exception as e:  # noqa: BLE001
                log.exception("reviewer crashed for %s", slug)
                self._store.add_comment(
                    slug, actor=review_by,
                    comment=f"reviewer crashed: {type(e).__name__}: {e}",
                )
                return
            self._store.add_comment(
                slug, actor=review_by,
                comment=(
                    f"review verdict: {'APPROVE' if verdict.approved else 'REJECT'}"
                    f" ({verdict.reason})"
                ),
            )
            if verdict.approved:
                self._store.move_task(
                    slug, schema.STATUS_DONE, actor=review_by,
                    detail="reviewer approved",
                )
                self._notify("review_approved", slug)
                # P6: after a task reaches done, check if all siblings of
                # its goal are also done — if so, spawn the verify ticket.
                self._spawn(self._check_goal_completion(slug))
                # #210: reviewed work is a candidate for skill capture.
                self._spawn(self._suggest_skills_bg(slug))
            else:
                # Send back to the original builder lane.
                self._store.move_task(
                    slug, schema.STATUS_READY, actor=review_by,
                    detail=f"reviewer rejected: {verdict.reason}",
                )
                self._notify("review_rejected", slug)

    async def _check_goal_completion(self, slug: str) -> None:
        """P6: if the task that just reached done belongs to a goal,
        check whether all siblings are done and spawn the verify ticket.
        Idempotent — the verify_spawned guard in maybe_spawn_verify makes
        repeated calls for the same goal/cycle harmless."""
        task = self._store.get_task(slug)
        if task is None:
            return
        goal_id = getattr(task, "goal_id", None)
        if not goal_id:
            # Also check the tags list as a fallback (tasks created before
            # the goal_id column existed may only have the tag).
            from gateway.crew_board.goal_loop import extract_goal_id
            goal_id = extract_goal_id(task.tags or [])
        if not goal_id:
            return
        try:
            from gateway.crew_board.goal_loop import maybe_spawn_verify
            spawned = maybe_spawn_verify(self._store, goal_id)
            if spawned:
                self._notify("goal_verify_spawned", slug)
        except Exception:  # noqa: BLE001
            log.exception("goal_loop: _check_goal_completion failed for %s", slug)

    async def _suggest_skills_bg(self, slug: str) -> None:
        """#210: when a task reaches DONE (reviewer-approved or auto-approved),
        analyze it for a reusable skill pattern and drop any suggestions into
        the Proposed lane (tagged 'skill') for the owner to approve. Idempotent
        via a per-task meta flag; best-effort (never raises into the loop)."""
        flag = f"skills_suggested:{slug}"
        try:
            if self._store.get_meta(flag):
                return
            task = self._store.get_task(slug)
            if task is None:
                return
            from gateway.crew_board.skills_suggest import suggest_skills
            sugg = await suggest_skills(self._store, task)
            for s in sugg:
                self._store.create_task(
                    title=f"[skill·{s['kind']}] {s['skill']}",
                    project_slug=task.project_slug, body=s["why"],
                    created_by="planner", tags=["skill", "from-review", s["kind"]],
                )
            self._store.set_meta(flag, "1")
            if sugg:
                self._notify("skills_suggested", slug)
        except Exception:  # noqa: BLE001
            log.exception("skills_suggest: background hook failed for %s", slug)

    def _notify(self, event: str, slug: str) -> None:
        if self._notifier is None:
            return
        try:
            self._notifier.broadcast({"event": event, "task": slug})
        except Exception:  # noqa: BLE001
            log.exception("notifier broadcast failed")
