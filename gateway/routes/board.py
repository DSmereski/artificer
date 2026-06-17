"""FastAPI routes for the Crew Board UI + API.

GET  /board                — HTML page (htmx + Tailwind via CDN)
GET  /board/state          — JSON snapshot for client refresh
POST /board/tasks          — create
POST /board/tasks/{slug}/move
POST /board/tasks/{slug}/assign
POST /board/tasks/{slug}/criteria
POST /board/tasks/{slug}/comment
POST /board/projects/{slug}/enable
POST /board/projects/{slug}/disable
WS   /board/events         — real-time updates
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

_PROJECTS_ROOT = Path(os.environ.get("HIVE_PROJECTS_ROOT", str(Path.home() / "projects")))

# Stats payload is cached briefly so repeated Stats-tab polls don't
# re-scan every task + up to 50 transcript files each refresh.
_STATS_TTL_S = 15.0

from fastapi import APIRouter, Body, Depends, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from gateway.crew_board import schema
from gateway.crew_board.store import CrewBoardStore, Project, Task

router = APIRouter(prefix="/board")

# Per-process random token embedded in the served board HTML.
# JS mutation calls send it as X-Board-Token; the server checks it.
# Flutter/G2 clients already send Bearer → they use path (b) and are unaffected.
_BOARD_TOKEN = secrets.token_urlsafe(32)

_bearer = HTTPBearer(auto_error=False)


def _require_board_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Allow if EITHER:
    (a) X-Board-Token header matches the per-process _BOARD_TOKEN, or
    (b) a valid device Bearer token is present (reuses DeviceStore.verify).
    GET endpoints are left open — only call this on mutating routes.
    """
    # Path (a): board-page session token
    x_token = request.headers.get("x-board-token", "")
    if x_token and secrets.compare_digest(x_token, _BOARD_TOKEN):
        return
    # Path (b): device Bearer token
    token = credentials.credentials if credentials else None
    if token:
        ai_team = getattr(request.app.state, "ai_team", None)
        if ai_team is not None:
            device = ai_team.devices.verify(token)
            if device is not None:
                ai_team.devices.touch(device.id)
                return
    raise HTTPException(
        status_code=403,
        detail="board mutation requires X-Board-Token or valid Bearer",
    )


def _require_board_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Auth for operational board controls (pause/resume).

    Same as ``_require_board_auth`` (X-Board-Token or device Bearer) but ALSO
    permits loopback callers — so the local restart script can drain/resume the
    dispatcher without embedding a secret (devices store only token *hashes*,
    so no plaintext bearer exists on disk for the script to send).

    Scope is deliberately narrow: ONLY pause/resume, which at worst let a local
    process idle the dispatcher (no data mutation). Task create/delete/move keep
    requiring ``_require_board_auth`` (token-gated).
    """
    from gateway.deps import _is_loopback

    client = request.client
    if client is not None and _is_loopback(client.host):
        return
    # Fall back to the standard token/bearer check.
    _require_board_auth(request, credentials)


_BOARD_CSP = (
    "default-src 'self'; "
    # Tailwind CDN script + inline scripts/handlers on the board page.
    "script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'; "
    # Inline styles + Google Fonts stylesheets (Inter / JetBrains Mono /
    # Material Icons) for the Hive ecosystem design system.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self' wss: ws:; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

# Embed-mode CSP: identical EXCEPT frame-ancestors is DROPPED entirely. The
# wallpaper dashboard frames /board?embed=1 from a DIFFERENT origin than the
# gateway — and crucially that parent is **file://** when Lively renders it,
# whose origin is the opaque/null origin. CSP `frame-ancestors *` matches only
# network-scheme ('http'/'https'/'ws'/'wss') ancestors, NOT file:// — so `*`
# still BLOCKS the wallpaper and the board cell renders black. There is no CSP
# token that reliably allows an opaque file:// ancestor, so we omit the
# directive entirely. Safe: /board is loopback-only (the gateway binds
# 127.0.0.1 + the Tailscale IP, never 0.0.0.0) and the embed page carries its
# own X-Board-Token, so only a local page can frame it regardless. Scoped to
# the embed response only — standalone /board keeps 'none'.
_BOARD_CSP_EMBED = _BOARD_CSP.replace(
    "frame-ancestors 'none'; ", ""
)


def _store(request: Request) -> CrewBoardStore:
    s = getattr(request.app.state, "crew_store", None)
    if s is None:
        raise HTTPException(503, "crew_board not initialised")
    return s


def _task_to_dict(t: Task) -> dict:
    return {
        "slug": t.slug, "title": t.title, "body": t.body,
        "status": t.status, "project_slug": t.project_slug,
        "assignee": t.assignee, "created_by": t.created_by,
        "priority": t.priority, "estimate": t.estimate,
        "acceptance_criteria": t.acceptance_criteria,
        "files_of_interest": t.files_of_interest,
        "depends_on": t.depends_on, "tags": t.tags,
        "attempt_count": t.attempt_count,
        "last_branch": t.last_branch, "last_pr_url": t.last_pr_url,
        "verify_results": t.verify_results,
        # Kanban features added this session — surfaced so the web
        # board + Flutter app can show review/smoke/polish state.
        "review_by": getattr(t, "review_by", None),
        "polish_iters": getattr(t, "polish_iters", None),
        "smoke_cmd": getattr(t, "smoke_cmd", None),
        # Content requests: kind='content' + the spec (incl. result_media_ids)
        # so the dashboard can render thumbnails + the content gallery.
        "kind": getattr(t, "kind", "code"),
        "content_spec": getattr(t, "content_spec", {}) or {},
        # Per-task token usage — tracked SEPARATELY, never combined. The
        # Flutter app shows distinct H (hive/Ollama) and C (claude) chips.
        "hive_tokens": int(getattr(t, "hive_tokens", 0) or 0),
        "claude_tokens": int(getattr(t, "claude_tokens", 0) or 0),
        # Live current action — what the agent is doing right now.
        "last_action": getattr(t, "last_action", None),
        "agent_turns": int(getattr(t, "agent_turns", 0) or 0),
        "smoke_ok": (t.verify_results or {}).get("smoke", {}).get(
            "exit_code", None
        ) == 0 if (t.verify_results or {}).get("smoke") else None,
        "created_at": t.created_at, "updated_at": t.updated_at,
    }


def _project_to_dict(p: Project) -> dict:
    return {
        "slug": p.slug, "path": p.path, "name": p.name,
        "enabled": p.enabled, "push_allowed": p.push_allowed,
        "test_cmd": p.test_cmd, "parallel": getattr(p, "parallel", False),
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


@router.post("/pause")
async def pause_board(
    request: Request,
    _auth: None = Depends(_require_board_admin),
) -> JSONResponse:
    """Stop the dispatcher from starting NEW hive work. In-flight tasks finish;
    the reaper still runs. Persisted so it survives gateway restart."""
    store = _store(request)
    store.set_paused(True)
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "board_paused", "paused": True})
    return JSONResponse({"paused": True})


@router.post("/resume")
async def resume_board(
    request: Request,
    _auth: None = Depends(_require_board_admin),
) -> JSONResponse:
    """Allow the dispatcher to start new work again."""
    store = _store(request)
    store.set_paused(False)
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "board_resumed", "paused": False})
    return JSONResponse({"paused": False})


_CONTENT_PROJECT = "content"


class ContentRequest(BaseModel):
    type: str = Field("image", pattern="^(image|video)$")
    prompt: str = Field(..., min_length=1, max_length=2000)
    count: int = Field(1, ge=1, le=4)
    width: int = Field(1024, ge=64, le=2048)
    height: int = Field(1024, ge=64, le=2048)
    negative_prompt: str = ""
    seed_media_id: str | None = None    # required for video (image→video)
    project_slug: str | None = None     # defaults to the virtual 'content' project


@router.post("/content")
async def create_content(
    body: ContentRequest,
    request: Request,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Create a content-generation request as a board task. The dispatcher's
    content handler runs it through the Image/Video shim and lands it in `done`
    with the result media attached (kind='content')."""
    store = _store(request)
    proj = body.project_slug or _CONTENT_PROJECT
    # Ensure the virtual content project exists (the dispatcher's content
    # handler ignores the project working tree, but tasks need a project).
    if store.get_project(proj) is None and proj == _CONTENT_PROJECT:
        store.upsert_project(Project(
            slug=_CONTENT_PROJECT, path=str(_PROJECTS_ROOT), name="Content",
            enabled=True, push_allowed=False, test_cmd=None,
        ))
    spec = {
        "type": body.type,
        "prompt": body.prompt,
        "count": body.count,
        "width": body.width,
        "height": body.height,
        "negative_prompt": body.negative_prompt,
        "state": "queued",
        "result_media_ids": [],
    }
    if body.seed_media_id:
        spec["seed_media_id"] = body.seed_media_id
    title = f"{body.type}: {body.prompt[:60]}"
    task = store.create_task(
        title=title, project_slug=proj, created_by="owner",
        kind="content", content_spec=spec, tags=["content", body.type],
    )
    store.assign_task(task.slug, "content", actor="owner")
    store.move_task(task.slug, schema.STATUS_READY, actor="owner",
                    detail="content request queued")
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "content_requested", "slug": task.slug})
    return JSONResponse({"slug": task.slug, "type": body.type})


@router.get("/session-token")
async def board_session_token(request: Request) -> JSONResponse:
    """Return the per-process board mutation token to LOOPBACK callers only.

    The wallpaper dashboard (same trust boundary as the local host) fetches this
    once so it can perform board mutations (pause/task/move/approve) with the
    X-Board-Token header, without us shipping a device Bearer into browser JS.
    Loopback-only: Tailscale/LAN/remote get 403 — they must use a device Bearer
    on the mutation itself, exactly as before.
    """
    from gateway.deps import _is_loopback

    client = request.client
    if client is None or not _is_loopback(client.host):
        raise HTTPException(403, "session-token is loopback-only")
    return JSONResponse({"token": _BOARD_TOKEN})


@router.get("/state")
async def get_state(request: Request) -> JSONResponse:
    store = _store(request)
    tasks = store.list_tasks()
    projects = store.list_projects()
    approvals = store.list_pending_approvals()
    return JSONResponse({
        "tasks": [_task_to_dict(t) for t in tasks],
        "projects": [_project_to_dict(p) for p in projects],
        "pending_approvals": approvals,
        "paused": store.is_paused(),
    })


@router.get("/stats")
async def get_stats(request: Request) -> JSONResponse:
    """Aggregate board metrics for the Stats tab. Tokens are reported
    SEPARATELY for hive vs claude — never summed into one number."""
    cached = getattr(request.app.state, "crew_stats_cache", None)
    now = time.monotonic()
    if cached is not None and (now - cached[0]) < _STATS_TTL_S:
        return JSONResponse(cached[1])
    store = _store(request)
    tasks = store.list_tasks()
    by_status: dict[str, int] = {}
    by_assignee: dict[str, int] = {}
    hive_tokens = 0
    claude_tokens = 0
    attempts_total = 0
    attempts_n = 0
    smoke_pass = smoke_fail = 0
    per_project: dict[str, dict] = {}
    for t in tasks:
        if t.status == "archived":
            # Count archived separately; don't let dead junk skew live stats.
            by_status["archived"] = by_status.get("archived", 0) + 1
            continue
        by_status[t.status] = by_status.get(t.status, 0) + 1
        by_assignee[t.assignee] = by_assignee.get(t.assignee, 0) + 1
        hive_tokens += int(getattr(t, "hive_tokens", 0) or 0)
        claude_tokens += int(getattr(t, "claude_tokens", 0) or 0)
        if t.attempt_count:
            attempts_total += t.attempt_count
            attempts_n += 1
        vr = t.verify_results or {}
        sm = vr.get("smoke") if isinstance(vr, dict) else None
        if isinstance(sm, dict) and sm.get("ran"):
            if sm.get("exit_code") == 0:
                smoke_pass += 1
            else:
                smoke_fail += 1
        pp = per_project.setdefault(
            t.project_slug,
            {"done": 0, "active": 0, "hive_tokens": 0, "claude_tokens": 0},
        )
        if t.status == "done":
            pp["done"] += 1
        elif t.status in ("ready", "in_progress", "qa", "review", "backlog", "proposed"):
            pp["active"] += 1
        pp["hive_tokens"] += int(getattr(t, "hive_tokens", 0) or 0)
        pp["claude_tokens"] += int(getattr(t, "claude_tokens", 0) or 0)
    # Top projects by activity (live + done), drop fully-archived.
    top = sorted(
        ((k, v) for k, v in per_project.items() if v["done"] + v["active"] > 0),
        key=lambda kv: kv[1]["done"] + kv[1]["active"], reverse=True,
    )[:12]
    # Avg tokens per task with non-zero spend, tracked SEPARATELY.
    hive_n = sum(1 for t in tasks if int(getattr(t, "hive_tokens", 0) or 0) > 0)
    claude_n = sum(
        1 for t in tasks if int(getattr(t, "claude_tokens", 0) or 0) > 0
    )
    # Parse-fail rate: fraction of agent turns the model emitted a non-
    # parseable tool call (transcript `call is None`). Should sit near 0
    # after P1 constrained decoding. Best-effort, bounded transcript scan.
    parse_fail = _parse_fail_rate(request)
    payload = {
        "by_status": by_status,
        "by_assignee": by_assignee,
        "tokens": {  # SEPARATE — never combined
            "hive": hive_tokens,
            "claude": claude_tokens,
        },
        "avg_tokens_per_task": {  # SEPARATE — never combined
            "hive": round(hive_tokens / hive_n) if hive_n else 0,
            "claude": round(claude_tokens / claude_n) if claude_n else 0,
        },
        "avg_attempts": round(attempts_total / attempts_n, 2) if attempts_n else 0,
        "smoke": {"pass": smoke_pass, "fail": smoke_fail},
        # Estimated $ — claude only (hive/Ollama is $0). Blended
        # ~$6/1M tokens (input+output combined, as stored).
        "cost_usd": round(claude_tokens / 1_000_000 * 6.0, 2),
        "lessons": store.count_lessons(),
        "parse_fail": parse_fail,
        "paused": store.is_paused(),
        "top_projects": [
            {"slug": k, **v} for k, v in top
        ],
    }
    request.app.state.crew_stats_cache = (now, payload)
    return JSONResponse(payload)


@router.get("/tokens-by-day")
async def get_tokens_by_day(
    request: Request, days: int = 30,
) -> JSONResponse:
    """Per-day token aggregation for the last *days* days (default 30).

    Returns a JSON array ascending by date, zero-filled so every day in
    the window is present.  Open read — no auth required, consistent with
    /board/stats.

    Schema: [{date: 'YYYY-MM-DD', hive: int, claude: int, total: int}, ...]
    """
    days = max(1, min(int(days), 365))
    store = _store(request)
    return JSONResponse(store.tokens_by_day(days=days))


@router.get("/tasks/{slug}/diff")
async def get_task_diff(request: Request, slug: str) -> JSONResponse:
    """Git diff of the commit the hive made for this task (matched by
    slug in the commit message), so the owner can review the actual
    changes before approving in REVIEW."""
    import re as _re
    import subprocess
    if not _re.fullmatch(r"T-\d{1,8}", slug):
        raise HTTPException(400, "bad slug")
    store = _store(request)
    task = store.get_task(slug)
    if task is None:
        raise HTTPException(404, "unknown task")
    proj = store.get_project(task.project_slug)
    if proj is None:
        return JSONResponse({"diff": "", "note": "no project"})
    try:
        # newest commit whose message contains the slug
        sha = subprocess.run(
            ["git", "log", "--grep", slug, "-n", "1", "--format=%H"],
            cwd=proj.path, capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        if not sha:
            return JSONResponse({"diff": "", "note": "no commit for this task yet"})
        diff = subprocess.run(
            ["git", "show", "--stat", "--patch", sha],
            cwd=proj.path, capture_output=True, text=True, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        return JSONResponse({"diff": "", "note": f"git error: {e}"})
    return JSONResponse({"sha": sha[:10], "diff": diff[:60000]})


@router.post("/self-improve")
async def self_improve(
    request: Request,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Mine the board for failure patterns (high parse-fail, max-attempts
    parks, repeat escalations) and create PROPOSED improvement tickets on
    the ai-team project for the owner to review. Dogfood: the pipeline
    surfaces its own weak spots. Does NOT auto-assign (gateway self-edits
    need owner sign-off)."""
    store = _store(request)
    proposals: list[tuple[str, str]] = []  # (title, body)
    tasks = store.list_tasks()
    # 1. parse-fail rate
    pf, tn = store.parse_fail_totals()
    if tn >= 50 and pf / tn > 0.15:
        proposals.append((
            "Investigate elevated hive parse-fail rate",
            f"Parse-fail rate is {round(100*pf/tn,1)}% ({pf}/{tn} turns) — "
            "above the 15% healthy bar. Check the Ollama tools/format path "
            "and whether the model is emitting non-tool-call output.",
        ))
    # 2. tasks parked at max attempts (in review after repeated failure)
    parked = [t for t in tasks
              if t.status == "review" and t.attempt_count >= 5]
    for t in parked:
        proposals.append((
            f"Triage stuck task {t.slug}",
            f"{t.slug} ('{t.title}') hit {t.attempt_count} attempts and is "
            f"parked in review on project {t.project_slug}. Review the "
            "transcript + last verdict; simplify the task or fix the gate.",
        ))
    # 3. heavy claude escalation spend on a project
    from collections import Counter
    esc = Counter(t.project_slug for t in tasks if (t.claude_tokens or 0) > 500_000)
    for proj, n in esc.items():
        if n >= 2:
            proposals.append((
                f"Reduce claude escalation on {proj}",
                f"{n} tasks on {proj} each burned >500k claude tokens — the "
                "hive struggled and escalated. Consider clearer task briefs, "
                "smaller tickets, or a relevant skill/lesson.",
            ))
    created = []
    for title, body in proposals[:8]:
        # proposed status (owner-created lands in backlog; we want proposed
        # for review) — create then move to proposed isn't needed; just
        # leave in backlog unassigned for the owner.
        t = store.create_task(title=f"[self-improve] {title}",
                              project_slug="ai-team", body=body,
                              created_by="system", tags=["self-improve"])
        created.append(t.slug)
    return JSONResponse({"found": len(proposals), "created": created})


@router.get("/lessons")
async def get_lessons(request: Request, limit: int = 50) -> JSONResponse:
    """All distilled hive lessons (the knowledge it learned from claude
    rescues) for the Stats lessons reader."""
    store = _store(request)
    out = []
    try:
        for p in store.list_projects():
            for ls in store.recent_lessons(p.slug, limit=limit):
                out.append({"project": p.slug, "body": ls.body,
                            "task": getattr(ls, "task_slug", "")})
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse(out[:limit])


def _parse_fail_rate(request: Request) -> dict:
    """Parse-fail rate from the DB counters the hive loop accumulates
    (store.record_turn / bump_parse_fail) — a single SUM, replacing the
    old per-poll scan of up to 50 transcript JSON files."""
    store = _store(request)
    fails, turns = store.parse_fail_totals()
    return {
        "turns": turns,
        "fails": fails,
        "rate": round(fails / turns, 4) if turns else 0.0,
    }


_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string"},
        "tickets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "criteria": {"type": "array", "items": {"type": "string"}},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "body", "criteria", "depends_on"],
            },
        },
    },
    "required": ["project_name", "tickets"],
}

_PLAN_SYSTEM = """You are a senior software project planner specialising in
autonomous-agent task decomposition. Given a one-line goal, output a JSON
plan with a project_name (short, kebab-friendly) and an ORDERED list of
4-9 build tickets. The tickets will be executed sequentially by a coding
agent; every ticket MUST be independently verifiable.

Rules for each ticket
─────────────────────
1. SINGLE CONCERN  — each ticket addresses exactly ONE thing (one model,
   one API endpoint, one UI screen, one integration step). Never bundle
   two distinct concerns in one ticket.

2. ACCEPTANCE CRITERIA (required, 2-4 per ticket)  — each criterion is a
   concrete, machine-testable assertion written in the imperative:
     • "pytest passes: test_X covers Y returning Z"
     • "file src/models/user.py exists and exports class User"
     • "GET /api/v1/users returns 200 with a JSON array"
   Avoid vague criteria like "works correctly" or "is well-tested".

3. DEPENDS_ON  — an array of 0-based ticket INDEXES this ticket depends
   on. Ticket 0 has no dependencies ([]). Ticket 3 that needs tickets 1
   and 2 writes [1, 2]. This creates the explicit dependency chain the
   dispatcher uses to gate execution.

4. FILES_OF_INTEREST  — list the specific relative file paths the ticket
   creates or modifies (e.g. "src/models/user.py", "tests/test_user.py").
   The agent uses this list to know what to write first.

5. BODY  — one focused paragraph of implementation detail: what class/
   function to write, which design pattern to follow, what the module
   boundary is. No fluff.

6. ORDER  — scaffolding and data models first, then business logic, then
   API/routes, then UI, then integration/E2E last.

Respond with JSON only — no prose, no markdown fences."""


def _greenfield_stack(text: str) -> dict[str, str]:
    """Detect the intended stack for a BRAND-NEW project from its goal text.

    For greenfield projects there is no existing code to sniff, so the planner
    used to get NO stack guidance and would hallucinate a Python/pytest mix into
    e.g. an Android app (the T-0360 wedge: Java sources + `python -m pytest`,
    verify can never pass, chain parks). This forces ONE real stack + a matching
    test runner. Returns {label, test_cmd, directive}.
    """
    t = f" {text.lower()} "

    def kit(label: str, test_cmd: str, files: str, scaffold: str | None,
            bad: tuple[str, ...]) -> dict:
        return {
            "label": label,
            "test_cmd": test_cmd,
            "scaffold_kind": scaffold,   # how to seed a runnable skeleton (decompose builds the cmd)
            "bad_globs": bad,            # file-path substrings that betray a WRONG stack
            "directive": (
                f"TARGET STACK (brand-new project): {label}. EVERY ticket MUST be "
                f"specced for THIS stack only — {files}; run tests with `{test_cmd}`. "
                f"Do NOT mix in another language or test runner (e.g. NO Python/"
                f"pytest files in an Android/Flutter/JS app). The acceptance-criteria "
                f"examples in the system prompt are illustrative — translate them to "
                f"this stack.\n\n"
            ),
        }

    if "flutter" in t or "dart" in t:
        return kit("Flutter/Dart", "flutter test", "lib/*.dart + test/*_test.dart",
                   "flutter", (".py", "src/main/java", ".kt "))
    if (("kotlin" in t or "java" in t) and "android" in t) or "gradle" in t:
        return kit("Android native (Gradle)", "gradlew.bat test",
                   "app/src/main/(java|kotlin) + app/src/test", "gradle",
                   (".py", ".dart"))
    if "android" in t or "ios" in t or "mobile app" in t:
        # Ambiguous mobile goal → Flutter (the house stack), never pytest.
        return kit("Flutter/Dart", "flutter test", "lib/*.dart + test/*_test.dart",
                   "flutter", (".py", "src/main/java", ".kt "))
    if any(k in t for k in ("react", "vue", "svelte", "next.js", "node", "express",
                            "typescript", "javascript", " web app", "website")):
        return kit("Node/TypeScript", "npm test", "src/*.(ts|js) + *.test.(ts|js)",
                   "node", (".py", ".dart", ".java"))
    if "rust" in t or "cargo" in t:
        return kit("Rust", "cargo test", "src/*.rs", "rust",
                   (".py", ".dart", ".java", ".js", ".ts"))
    if "golang" in t or " go " in t:
        return kit("Go", "go test ./...", "*.go", "go", (".py", ".dart", ".java"))
    if "godot" in t or "gdscript" in t:
        return kit("Godot/GDScript", "echo no-tests", "*.gd + scenes", None, (".py",))
    return kit("Python", "python -m pytest -q", "*.py + tests/test_*.py", "python",
               (".dart", ".java", "src/main/java"))


def _plan_stack_violations(plan: dict, bad_globs: tuple[str, ...]) -> list[str]:
    """Return wrong-stack files the planner put in the tickets.

    The fail-fast guard: a plan that mixes stacks (e.g. .java + pytest in a
    Flutter project) is rejected at decompose time instead of being discovered
    after the hive burns ~35 turns and parks the chain (the T-0360 wedge)."""
    hits: list[str] = []
    for tk in (plan.get("tickets") or []):
        files = [str(f) for f in (tk.get("files") or [])]
        # also catch a pytest test command hidden in the body/criteria
        blob = " ".join(files + [str(c) for c in (tk.get("criteria") or [])]).lower()
        for f in files:
            fl = f.lower()
            if any(b.strip() and b.strip() in fl for b in bad_globs):
                hits.append(f)
        if "pytest" in blob and any(b.strip() == ".py" for b in bad_globs):
            hits.append(f"{tk.get('title','?')}: pytest")
    return hits


def _scaffold_stack_skeleton(gf: dict | None, path: "Path", slug: str) -> None:
    """Seed a RUNNABLE project skeleton for the detected stack so the very first
    `test_cmd` can execute (e.g. `flutter test` needs a pubspec; without this the
    first verify fails for a MISSING project, not a real bug). Best-effort: a
    missing toolchain (flutter/cargo/go not installed) is logged + skipped, never
    fatal — the hive can still scaffold from inside the build."""
    import logging
    import re as _re
    import subprocess
    log = logging.getLogger("gateway.board")
    if not gf:
        return
    kind = gf.get("scaffold_kind")
    pkg = _re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_") or "app"

    def run(cmd: str) -> None:
        try:  # shell=True so Windows .cmd/.bat shims (flutter, npm) resolve on PATH
            subprocess.run(cmd, cwd=str(path), timeout=240, shell=True,
                           capture_output=True)
        except Exception as e:  # noqa: BLE001
            log.warning("greenfield scaffold (%s) skipped: %s", kind, e)

    if kind == "flutter":
        run(f"flutter create . --project-name {pkg}")
    elif kind == "node":
        run("npm init -y")
        try:
            import json as _j
            pj = path / "package.json"
            data = _j.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}
            data.setdefault("scripts", {})["test"] = "node --test"  # built-in runner
            pj.write_text(_j.dumps(data, indent=2), encoding="utf-8")
            (path / "test").mkdir(exist_ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("node scaffold patch skipped: %s", e)
    elif kind == "rust":
        run(f"cargo init --name {pkg}")
    elif kind == "go":
        run(f"go mod init {pkg}")
    elif kind == "python":
        try:
            (path / "tests").mkdir(exist_ok=True)
            (path / "tests" / "__init__.py").touch()
        except OSError as e:
            log.warning("python scaffold skipped: %s", e)
    # gradle / godot / None: no reliable headless scaffold — the hive handles it.


@router.post("/decompose")
async def decompose_goal(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """NL goal → an LLM-generated, dependency-chained ticket plan, created
    on the board ready for the hive. Scaffolds a new project (local dir +
    git + enabled + push_allowed) when no project_slug is given."""
    import json as _json
    import re as _re
    import subprocess
    from gateway.crew_board import schema as _schema
    from gateway.crew_board.store import Project as _Project
    from gateway.helpers.base import OllamaInvoker, extract_json, SchemaValidationError

    store = _store(request)
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise HTTPException(400, "goal required")
    project_slug = str(payload.get("project_slug", "")).strip()

    # When targeting an EXISTING project, detect its real stack and force the
    # planner to spec tickets for THAT stack. Without this the planner's Python/
    # web examples bias it into writing e.g. Vue components + pytest into a
    # Flutter/Dart app (the T-0351 failure that wedged the board for 85 turns).
    existing_proj = store.get_project(project_slug) if project_slug else None
    # gf = the detected stack for a GREENFIELD project (None for existing ones).
    # The SAME object drives the planner directive, the project test_cmd, the
    # skeleton scaffold, AND the mixed-stack guard — so they can never disagree.
    gf = None if existing_proj is not None else _greenfield_stack(goal)
    if existing_proj is not None:
        from gateway.crew_board.claude_runner import _stack_hint
        hint = _stack_hint(existing_proj.path)
        stack_directive = (
            f"TARGET PROJECT STACK: {hint}. The project already exists at "
            f"{existing_proj.path}. EVERY ticket MUST match this stack — its real "
            f"file types, directory layout, and test runner. Do NOT introduce a "
            f"different language or framework: e.g. NO Python/pytest, Vue, or React "
            f"files in a Flutter/Dart app (use lib/*.dart + `flutter test`). The "
            f"acceptance-criteria examples in the system prompt are illustrative — "
            f"translate them to this stack.\n\n"
        )
    else:
        stack_directive = gf["directive"]

    # Generate the plan (planner-qwen — fast, doesn't compete with the hive
    # coder lane on qwen3.6:27b).
    async def _plan(extra: str = "") -> dict:
        text, _, _ = await OllamaInvoker().chat(
            model="planner-qwen", system=_PLAN_SYSTEM,
            user=f"{stack_directive}{extra}Goal: {goal}",
            params={"temperature": 0.3, "num_ctx": 8192, "num_predict": 4096},
            fmt=_PLAN_SCHEMA,
        )
        p = extract_json(text)
        if not isinstance(p, dict) or not p.get("tickets"):
            raise ValueError("planner returned no tickets")
        return p

    try:
        plan = await _plan()
        # Fail-fast mixed-stack GUARD (greenfield): if the planner snuck in
        # wrong-stack files, re-plan ONCE with a harder directive, then REFUSE —
        # never create a chain that can't pass verify (the T-0360 35-turn wedge).
        if gf is not None:
            bad = _plan_stack_violations(plan, gf["bad_globs"])
            if bad:
                plan = await _plan(extra=(
                    f"Your previous plan used WRONG-stack files: {', '.join(bad[:6])}. "
                    f"Those do NOT belong in a {gf['label']} project. Re-plan using "
                    f"ONLY {gf['label']} files + `{gf['test_cmd']}`.\n\n"))
                bad = _plan_stack_violations(plan, gf["bad_globs"])
                if bad:
                    raise HTTPException(422, (
                        f"planner kept producing wrong-stack files for a "
                        f"{gf['label']} project ({', '.join(bad[:6])}); refusing to "
                        f"create a chain that can never pass verify. Re-word the goal "
                        f"or target an existing project."))
    except HTTPException:
        raise
    except (SchemaValidationError, Exception) as e:  # noqa: BLE001
        raise HTTPException(502, f"planner failed: {e}")

    scaffolded = False
    # test_cmd comes from the SAME gf detector as the directive + guard (greenfield)
    # so the project's test runner matches the stack every ticket was specced for.
    if existing_proj is not None and getattr(existing_proj, "test_cmd", ""):
        test_cmd = existing_proj.test_cmd
    else:
        test_cmd = gf["test_cmd"]
    if not project_slug:
        name = str(plan.get("project_name") or "new-project")
        slug = _re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "new-project"
        path = _PROJECTS_ROOT / _re.sub(r"[^A-Za-z0-9]+", "", name) or (_PROJECTS_ROOT / "NewProject")
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not (path / ".git").exists():
                subprocess.run(["git", "init", "-q"], cwd=str(path), timeout=20)
                (path / "README.md").write_text(f"# {name}\n\n{goal}\n",
                                                encoding="utf-8")
            # Seed a RUNNABLE skeleton for the detected stack so the very first
            # `test_cmd` can execute (e.g. `flutter test` needs a pubspec — without
            # this the first verify fails for a missing project, not a real bug).
            _scaffold_stack_skeleton(gf, path, slug)
            if not (path / ".git" / "refs" / "heads").exists() or True:
                subprocess.run(["git", "add", "-A"], cwd=str(path), timeout=30)
                subprocess.run(["git", "commit", "-q", "-m", "init"],
                               cwd=str(path), timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            raise HTTPException(500, f"scaffold failed: {e}")
        store.upsert_project(_Project(slug=slug, path=str(path), name=name,
                                      test_cmd=test_cmd))
        store.set_project_enabled(slug, enabled=True)
        store.set_project_push_allowed(slug, allowed=True)
        project_slug = slug
        scaffolded = True
    elif store.get_project(project_slug) is None:
        raise HTTPException(404, f"unknown project {project_slug!r}")

    # Create the chained tickets.
    # First pass: create all tasks (no depends_on yet — we need the slug
    # list to translate 0-based LLM indexes into real task slugs).
    slugs, titles, raw_tickets = [], [], []
    for t in plan["tickets"][:12]:
        title = str(t.get("title", "")).strip()[:120]
        if not title:
            continue
        crit = [{"text": str(c), "checked": False}
                for c in (t.get("criteria") or [])][:6]
        task = store.create_task(
            title=title, project_slug=project_slug,
            body=str(t.get("body", "")), created_by="owner",
            acceptance_criteria=crit,
            files_of_interest=[str(f) for f in (t.get("files") or [])][:8],
            tags=["nl-decompose"], review_by="claude-code",
        )
        slugs.append(task.slug)
        titles.append(title)
        raw_tickets.append(t)
    # Second pass: wire depends_on.  Use the LLM-supplied 0-based index
    # list when EXPLICITLY present and valid; fall back to the sequential
    # chain (each ticket depends on the previous) when the key is absent
    # or contains out-of-range values.  An empty list [] is only accepted
    # as-is for ticket 0 (no prior ticket); for ticket i>0 an explicit
    # empty list also wins (the planner decided no dependency is needed).
    for i, (sl, t) in enumerate(zip(slugs, raw_tickets)):
        key_present = "depends_on" in t
        llm_deps = t.get("depends_on")
        if (
            key_present
            and isinstance(llm_deps, list)
            and all(isinstance(d, int) and 0 <= d < len(slugs) for d in llm_deps)
        ):
            deps = [slugs[d] for d in llm_deps]
        else:
            # Fallback: linear chain
            deps = [slugs[i - 1]] if i > 0 else []
        store._conn.execute("UPDATE crew_tasks SET depends_on=? WHERE slug=?",
                            (_json.dumps(deps), sl))
    store._conn.commit()
    for sl in slugs:
        store.assign_task(sl, "hive", actor="owner")
        store.move_task(sl, _schema.STATUS_READY, actor="owner",
                        detail="NL-decomposed plan")
    return JSONResponse({
        "project_slug": project_slug, "scaffolded": scaffolded,
        "created": len(slugs), "titles": titles,
    })


@router.post("/tasks")
async def create_task(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    title = str(payload.get("title", "")).strip()
    project_slug = str(payload.get("project_slug", "")).strip()
    if not title or not project_slug:
        raise HTTPException(400, "title and project_slug required")
    try:
        t = store.create_task(
            title=title,
            project_slug=project_slug,
            body=str(payload.get("body", "")),
            created_by=str(payload.get("created_by", "owner")),
            priority=str(payload.get("priority", "medium")),
            estimate=payload.get("estimate"),
            acceptance_criteria=payload.get("acceptance_criteria") or [],
            files_of_interest=payload.get("files_of_interest") or [],
            depends_on=payload.get("depends_on") or [],
            tags=payload.get("tags") or [],
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "task_created", "task": t.slug})
    return JSONResponse(_task_to_dict(t))


@router.post("/tasks/{slug}/move")
async def move_task(
    request: Request,
    slug: str,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    to_status = str(payload.get("status", "")).strip()
    actor = str(payload.get("actor", "owner"))
    try:
        t = store.move_task(slug, to_status, actor=actor)
    except ValueError as e:
        raise HTTPException(400, str(e))
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "task_moved", "task": slug, "status": to_status})
    return JSONResponse(_task_to_dict(t))


@router.post("/tasks/{slug}/assign")
async def assign_task(
    request: Request,
    slug: str,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    try:
        t = store.assign_task(
            slug, str(payload.get("assignee", "none")),
            actor=str(payload.get("actor", "owner")),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(_task_to_dict(t))


@router.post("/tasks/{slug}/criteria")
async def update_criteria(
    request: Request,
    slug: str,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    crit = payload.get("acceptance_criteria")
    if not isinstance(crit, list):
        raise HTTPException(400, "acceptance_criteria must be a list")
    t = store.update_acceptance_criteria(slug, crit)
    return JSONResponse(_task_to_dict(t))


@router.post("/tasks/{slug}/comment")
async def add_comment(
    request: Request,
    slug: str,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    actor = str(payload.get("actor", "owner"))
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    entry = store.add_comment(slug, actor=actor, comment=text)
    return JSONResponse({
        "task_slug": entry.task_slug,
        "actor": entry.actor,
        "action": entry.action,
        "detail": entry.detail,
        "created_at": entry.created_at,
    })


@router.post("/tasks/{slug}/unstuck")
async def unstuck_task(
    request: Request,
    slug: str,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Bring Claude in to diagnose + push along a STUCK ticket.

    Kicks off a background `claude` run that reads the project, diagnoses the
    real reason the hive stalled, and either fixes it in the real stack or
    explains why it can't be done as specced (without fabricating a fake stack
    to pass tests). While it works the task is parked in_progress under
    assignee 'claude'; on completion Claude's summary is posted as a comment and
    the task moves to review for the owner to decide the next step. Returns
    immediately (202-style) — the run continues in the background."""
    import asyncio as _asyncio
    from gateway.crew_board import schema as _schema

    store = _store(request)
    t = store.get_task(slug)
    if t is None:
        raise HTTPException(404, f"unknown task {slug!r}")
    if t.status in (_schema.STATUS_DONE, _schema.STATUS_ARCHIVED):
        raise HTTPException(400, "task is already done/archived")
    project = store.get_project(t.project_slug)
    if project is None:
        raise HTTPException(404, f"unknown project {t.project_slug!r}")

    notifier = getattr(request.app.state, "crew_notifier", None)

    # Park it in_progress under 'claude' so the board shows it's being worked.
    # A stuck task is usually in `backlog` (parked after the attempt cap) or
    # `qa`/`review`/`proposed` — none of which can jump STRAIGHT to in_progress
    # under ALLOWED_TRANSITIONS. Walk there via legal hops (else the unstuck
    # 500s exactly on the parked tasks it exists to rescue).
    store.assign_task(slug, "claude-code", actor="owner")  # "claude" is not a valid ASSIGNEE
    _ip = _schema.STATUS_IN_PROGRESS
    _route_to_in_progress = {
        _schema.STATUS_PROPOSED: [_schema.STATUS_BACKLOG, _schema.STATUS_READY, _ip],
        _schema.STATUS_BACKLOG:  [_schema.STATUS_READY, _ip],
        _schema.STATUS_READY:    [_ip],
        _schema.STATUS_QA:       [_schema.STATUS_READY, _ip],
        _schema.STATUS_REVIEW:   [_ip],
        _ip:                     [],
    }.get(t.status)
    if _route_to_in_progress is None:
        raise HTTPException(400, f"cannot unstick a task in status {t.status!r}")
    for _st in _route_to_in_progress:
        store.move_task(slug, _st, actor="owner", detail="claude unstuck started")
    store.add_comment(slug, actor="claude",
                      comment="[unstuck] Claude is diagnosing this ticket…")
    if notifier is not None:
        notifier.broadcast({"event": "task_moved", "task": slug,
                            "status": _schema.STATUS_IN_PROGRESS})

    async def _run() -> None:
        from gateway.crew_board.claude_runner import run_claude_unstuck
        try:
            res = await run_claude_unstuck(store, t, project=project)
            if res.tokens:
                try:
                    store.add_tokens(slug, kind="claude", n=res.tokens)
                except Exception:  # noqa: BLE001
                    pass
            summary = (res.stdout_tail or res.reason or "(no output)").strip()
            store.add_comment(slug, actor="claude",
                              comment=f"[unstuck] {summary[-1800:]}")
            store.move_task(slug, _schema.STATUS_REVIEW, actor="claude",
                            detail="claude unstuck done")
        except Exception as e:  # noqa: BLE001
            store.add_comment(slug, actor="claude",
                              comment=f"[unstuck] failed: {e}")
            store.move_task(slug, _schema.STATUS_BACKLOG, actor="claude",
                            detail="claude unstuck error")
        if notifier is not None:
            notifier.broadcast({"event": "task_unstuck_done", "task": slug})

    _asyncio.create_task(_run())
    return JSONResponse({"status": "unsticking", "slug": slug})


@router.get("/tasks/{slug}/transcript")
async def get_transcript(request: Request, slug: str) -> JSONResponse:
    """Full turn-by-turn agent transcript for a task (from
    vault/.crew_transcripts/<slug>.json) so the board can show the whole
    session, not just the latest action. Returns [] if none."""
    import re as _re
    if not _re.fullmatch(r"T-\d{1,8}", slug):
        raise HTTPException(400, "bad slug")
    vault = getattr(request.app.state, "crew_vault_path", None)
    if vault is None:
        return JSONResponse([])
    p = Path(vault) / ".crew_transcripts" / f"{slug}.json"
    if not p.is_file():
        return JSONResponse([])
    try:
        if p.stat().st_size > 4_000_000:
            return JSONResponse([{"turn": 0, "note": "transcript too large"}])
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return JSONResponse([])
    if not isinstance(data, list):
        return JSONResponse([])
    # Compact each turn for the UI: turn, tool+target (or parse-error), ok.
    out = []
    for e in data[-200:]:
        if not isinstance(e, dict):
            continue
        call = e.get("call")
        res = e.get("result") or {}
        if call:
            target = (call.get("args", {}) or {}).get("path") \
                or (call.get("args", {}) or {}).get("cmd") \
                or (call.get("args", {}) or {}).get("name") or ""
            label = f"{call.get('tool', '?')} {target}".strip()
        else:
            label = "(parse error)"
        out.append({
            "turn": e.get("turn"),
            "label": label,
            "ok": res.get("ok"),
        })
    return JSONResponse(out)


@router.get("/tasks/{slug}/audit")
async def get_audit(request: Request, slug: str) -> JSONResponse:
    store = _store(request)
    items = store.audit_for(slug)
    return JSONResponse([
        {
            "task_slug": e.task_slug,
            "actor": e.actor,
            "action": e.action,
            "detail": e.detail,
            "metadata": e.metadata,
            "created_at": e.created_at,
        }
        for e in items
    ])


@router.post("/projects/create")
async def create_project(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Create a brand-new project on disk (mkdir + git init) and
    register it as enabled. Used when the owner asks the board to
    spawn a fresh repo."""
    from gateway.crew_board.project_scanner import ensure_project_for_path
    store = _store(request)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name required")
    # Path is either explicit or derived from the configured projects root.
    # SECURITY: the resolved path MUST stay under an allowed project
    # root — otherwise a client could mkdir + git init (and later run an
    # autonomous agent) anywhere the gateway process can write.
    allowed_root = _PROJECTS_ROOT.resolve()
    raw_path = payload.get("path")
    if raw_path:
        path = Path(str(raw_path))
    else:
        path = allowed_root / name
    try:
        resolved = path.resolve()
        resolved.relative_to(allowed_root)
    except (ValueError, OSError):
        raise HTTPException(
            400, f"project path must be under {allowed_root}",
        )
    p = ensure_project_for_path(store, resolved, enabled=True)
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "project_created", "project": p.slug})
    return JSONResponse(_project_to_dict(p))


@router.post("/projects/{slug}/enable")
async def enable_project(
    request: Request,
    slug: str,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    store.set_project_enabled(slug, enabled=True)
    return JSONResponse(_project_to_dict(store.get_project(slug)))  # type: ignore[arg-type]


@router.post("/projects/{slug}/disable")
async def disable_project(
    request: Request,
    slug: str,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    store.set_project_enabled(slug, enabled=False)
    return JSONResponse(_project_to_dict(store.get_project(slug)))  # type: ignore[arg-type]


@router.post("/projects/{slug}/push_allowed")
async def set_push(
    request: Request,
    slug: str,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    store.set_project_push_allowed(
        slug, allowed=bool(payload.get("allowed", False)),
    )
    return JSONResponse(_project_to_dict(store.get_project(slug)))  # type: ignore[arg-type]


@router.delete("/tasks/{slug}")
@router.post("/tasks/{slug}/delete")
async def delete_task(
    request: Request,
    slug: str,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Hard-delete a task and all its audit/approval/lesson child rows.

    Accepts both DELETE /board/tasks/{slug} (REST clients, Flutter) and
    POST /board/tasks/{slug}/delete (web UI which can't easily send DELETE).

    Returns 404 if the slug does not exist.
    Returns {"deleted": slug} on success.

    If the task is currently in_progress the delete still succeeds — the
    dispatcher's get_task() will return None on the next tick and the
    running coroutine will bail out gracefully.
    """
    import re as _re
    if not _re.fullmatch(r"T-\d{1,8}", slug):
        raise HTTPException(400, "bad slug")
    store = _store(request)
    deleted = store.delete_task(slug)
    if not deleted:
        raise HTTPException(404, f"task {slug!r} not found")
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "task_deleted", "task": slug})
    return JSONResponse({"deleted": slug})


@router.post("/approvals/{approval_id}/resolve")
async def resolve_approval(
    request: Request,
    approval_id: int,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    store = _store(request)
    approved = bool(payload.get("approved", False))
    try:
        store.resolve_approval(approval_id, approved=approved)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return JSONResponse({"ok": True, "approved": approved})


@router.websocket("/events")
async def board_events(websocket: WebSocket) -> None:
    notifier = getattr(websocket.app.state, "crew_notifier", None)
    if notifier is None:
        await websocket.close(code=1011, reason="notifier missing")
        return
    await websocket.accept()
    await notifier.subscribe(websocket)
    try:
        while True:
            # Drain incoming pings; we don't expect inbound traffic.
            await websocket.receive_text()
    except Exception:  # noqa: BLE001
        pass
    finally:
        await notifier.unsubscribe(websocket)


@router.get("", response_class=HTMLResponse)
async def board_page(request: Request) -> HTMLResponse:
    """Serve the crew board.

    ``?embed=1`` returns the same page in embed mode: the <header> toolbar is
    hidden and <main> fills 100% height so the board fits inside a dashboard
    iframe cell. Embed mode also relaxes the CSP frame-ancestors from 'none' to
    'self' so the same-origin (127.0.0.1) wallpaper dashboard can frame it; the
    standalone page keeps 'none' (clickjacking defence). The board's own JS, WS,
    and X-Board-Token meta are identical in both modes — the iframe mutates with
    its own token, so the dashboard needs no auth wiring.
    """
    embed = request.query_params.get("embed") in ("1", "true", "yes")
    body_class = "embed" if embed else ""
    html = (
        _BOARD_HTML
        .replace("{{BOARD_TOKEN}}", _BOARD_TOKEN)
        .replace("{{BODY_CLASS}}", body_class)
    )
    csp = _BOARD_CSP_EMBED if embed else _BOARD_CSP
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


_BOARD_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="board-token" content="{{BOARD_TOKEN}}"/>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cg fill='%23E0A445'%3E%3Cpath d='M7 3h4l2 3.5L11 10H7L5 6.5z'/%3E%3Cpath d='M14 3h4l2 3.5L18 10h-4l-2-3.5z' opacity='.55'/%3E%3Cpath d='M7 13h4l2 3.5L11 20H7l-2-3.5z' opacity='.55'/%3E%3Cpath d='M14 13h4l2 3.5L18 20h-4l-2-3.5z'/%3E%3C/g%3E%3C/svg%3E"/>
<title>Crew Board</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet"/>
<style>
  /* Hive ecosystem theme — warm near-black base, copper/amber accents,
     green=live, cyan=telemetry/claude, red=error. OKLCH per DESIGN.md;
     never pure #000/#fff. Aligned with the Flutter app + dashboard. */
  :root {
    --bg:       oklch(0.14 0.014 55);
    --panel:    oklch(0.17 0.016 58);
    --card:     oklch(0.20 0.018 60);
    --card-hi:  oklch(0.24 0.020 60);
    --line:     oklch(0.30 0.02  64);
    --txt:      oklch(0.95 0.012 72);
    --txt-dim:  oklch(0.74 0.014 72);
    --faint:    oklch(0.56 0.014 68);
    --copper:   oklch(0.74 0.13  56);
    --accent:   oklch(0.83 0.15  78);   /* amber — primary accent/CTA */
    --amber-glow:oklch(0.85 0.17 80);
    --green:    oklch(0.80 0.16 150);   /* hive / live / healthy */
    --cyan:     oklch(0.80 0.10 200);   /* telemetry + claude data */
    --red:      oklch(0.66 0.17  25);   /* error / escalation */
    --on-amber: #1A0E00;                /* chocolate ink on amber fills */
    --font-ui:  'Inter', ui-sans-serif, system-ui, sans-serif;
    --font-mono:'JetBrains Mono', ui-monospace, monospace;
  }
  /* Themed scrollbars — match the warm-black/copper theme (and the embedding
     dashboard) instead of the OS default grey, which clashes when the board is
     framed on the wallpaper. Firefox via scrollbar-color; WebKit via ::-webkit. */
  * { scrollbar-width: thin; scrollbar-color: var(--line) transparent; }
  *::-webkit-scrollbar { width: 8px; height: 8px; }
  *::-webkit-scrollbar-track { background: transparent; }
  *::-webkit-scrollbar-thumb {
    background: oklch(0.30 0.02 64 / 0.7); border-radius: 8px;
    border: 2px solid transparent; background-clip: padding-box;
  }
  *::-webkit-scrollbar-thumb:hover { background: var(--copper); background-clip: padding-box; }
  *::-webkit-scrollbar-corner { background: transparent; }
  body { font-family: var(--font-ui);
         background:
           radial-gradient(120% 80% at 50% -10%, oklch(0.17 0.03 60), oklch(0.10 0.01 55)),
           var(--bg);
         color: var(--txt); -webkit-font-smoothing: antialiased; }
  /* Section header (klabel): uppercase amber + trailing hairline rule. */
  .klabel { display: flex; align-items: center; gap: 10px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.16em;
    text-transform: uppercase; color: var(--accent); margin-bottom: 10px; }
  .klabel::after { content: ''; flex: 1; height: 1px;
    background: linear-gradient(90deg, var(--line), transparent); }
  .klabel .note { font-family: var(--font-mono); font-weight: 500;
    letter-spacing: 0; text-transform: none; color: var(--faint); font-size: 10px; }
  .num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  .logo-mark { font-family: 'Material Icons Outlined'; font-size: 22px;
    color: var(--accent); line-height: 1; user-select: none;
    text-shadow: 0 0 12px oklch(0.85 0.17 80 / 0.5); }
  .liveact { margin-top: 6px; font-size: 11px; color: var(--accent);
    font-family: var(--font-mono); display: flex; align-items: center;
    gap: 6px; opacity: .92; }
  .livedot { width: 7px; height: 7px; border-radius: 50%;
    background: var(--accent); box-shadow: 0 0 6px var(--accent);
    animation: livepulse 1.6s ease-in-out infinite; }
  @keyframes livepulse { 0%,100% { opacity: .5; transform: scale(0.9); } 50% { opacity: 1; transform: scale(1.15); } }
  @media (prefers-reduced-motion: reduce) { .livedot { animation: none; } }
  .col { min-height: 70vh; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; transition: border-color .12s, background .12s; }
  .card:hover { background: var(--card-hi); border-color: var(--accent); }
  .chip { border-radius: 6px; padding: 1px 6px; font-size: 11px; }
  .chip.num { font-variant-numeric: tabular-nums; }
  .tab { padding: 4px 14px; border-radius: 8px; font-size: 13px; cursor: pointer; color: var(--txt-dim); }
  .tab.active { background: var(--card); color: var(--accent); }
  .btn { background: var(--card); border: 1px solid var(--line); color: var(--txt); border-radius: 8px; padding: 4px 12px; font-size: 13px; cursor: pointer; }
  .btn:hover { background: var(--card-hi); }
  .btn-primary { background: var(--accent); color: var(--on-amber); border: none; font-weight: 600; }
  dialog { background: var(--panel); color: var(--txt); border: 1px solid var(--line); border-radius: 12px; }
  dialog::backdrop { background: oklch(0.10 0.01 55 / 0.6); }
  pre { background: oklch(0.12 0.012 58) !important; color: var(--txt-dim); border-color: var(--line) !important; }
  .stat-num { font-size: 28px; font-weight: 700; color: var(--accent);
    font-family: var(--font-mono); font-variant-numeric: tabular-nums; line-height: 1; }
  .stat-lbl { font-size: 11px; font-weight: 600; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--faint); }
  .statcard { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
  /* Status accent tokens — shared with the Flutter app + dashboard. */
  .tok-hive   { color: var(--green); }
  .tok-claude { color: var(--cyan);  }
  /* Embed mode (?embed=1): inside a dashboard iframe cell. Hide the toolbar
     chrome, let the board fill the cell, trim outer padding, shrink columns
     to fit a band rather than 70vh. */
  body.embed > header { display: none !important; }
  body.embed { background: transparent; }
  body.embed main { height: 100vh; max-width: none !important;
    padding: 8px 10px !important; overflow: auto; }
  body.embed .col { min-height: 0; height: 100%; }
</style>
</head><body class="{{BODY_CLASS}}">
<header class="px-6 py-3 flex items-center gap-3 flex-wrap" style="background:var(--panel);border-bottom:1px solid var(--line)">
  <span class="logo-mark">hive</span>
  <h1 class="text-base font-semibold" style="color:var(--txt);letter-spacing:.02em">Crew Board</h1>
  <span id="badge" class="chip hidden num" style="background:oklch(0.83 0.15 78 / 0.14);color:var(--accent);border:1px solid oklch(0.83 0.15 78 / 0.4)"></span>
  <div class="flex gap-1 ml-3">
    <div id="tab-board" class="tab active" onclick="switchTab('board')">Board</div>
    <div id="tab-stats" class="tab" onclick="switchTab('stats')">Stats</div>
  </div>
  <select id="projFilter" onchange="FILTER_PROJ=this.value;render()" class="btn" title="filter by project"></select>
  <input id="search" oninput="FILTER_Q=this.value.toLowerCase();render()" placeholder="search…" class="btn" style="width:130px"/>
  <button onclick="openGoal()" class="btn" title="describe a goal → auto tickets">✦ Goal</button>
  <button id="pauseBtn" onclick="togglePause()" class="btn" title="pause/resume dispatcher">⏸ Pause</button>
  <button onclick="openCreate()" class="btn btn-primary ml-auto">+ Task</button>
  <button onclick="openProjects()" class="btn">Projects</button>
  <button onclick="selfImprove()" class="btn" title="mine failures → propose fix tickets">⚘ Improve</button>
  <label class="text-xs flex items-center gap-1" style="color:var(--txt-dim)"><input type="checkbox" id="notify" onchange="NOTIFY=this.checked"/>notify</label>
  <button onclick="refreshAll()" class="btn" title="refresh">↻</button>
</header>
<div id="pauseBanner" class="hidden px-6 py-2 text-sm font-semibold" style="background:oklch(0.83 0.15 78 / 0.10);border-bottom:1px solid var(--accent);color:var(--accent)">
  PAUSED: dispatcher will not start new work. In-flight tasks finish; reaper still runs.
</div>
<div id="nowBuilding" class="px-6 py-2 hidden" style="background:oklch(0.74 0.13 56 / 0.07);border-bottom:1px solid var(--line)"></div>
<main id="view-board" class="p-4 grid grid-cols-7 gap-3" id="columns"></main>
<main id="view-stats" class="p-6 hidden"></main>

<dialog id="dlg" class="p-0 rounded-md w-[640px] max-w-full"></dialog>

<script>
const COLUMNS = ["proposed","backlog","ready","in_progress","qa","review","done"];
const STATE = {tasks: [], projects: [], pending_approvals: []};
let SOCK = null;
let TAB = 'board';
let FILTER_PROJ = '';   // '' = all projects
let FILTER_Q = '';      // search query (lowercased)
let NOTIFY = false;     // browser notifications on review_ready/escalated
let BOARD_PAUSED = false; // mirrors board.paused from /state
const SEEN_EVENTS = new Set();
// Per-process session token — sent as X-Board-Token on every mutation.
const BOARD_TOKEN = document.querySelector('meta[name="board-token"]').content;
function _mutHeaders(extra) {
  return Object.assign({'content-type':'application/json','x-board-token':BOARD_TOKEN}, extra||{});
}

function switchTab(t) {
  TAB = t;
  document.getElementById('tab-board').classList.toggle('active', t==='board');
  document.getElementById('tab-stats').classList.toggle('active', t==='stats');
  document.getElementById('view-board').classList.toggle('hidden', t!=='board');
  document.getElementById('view-stats').classList.toggle('hidden', t!=='stats');
  if (t==='stats') loadStats();
}
function refreshAll() { loadState(); if (TAB==='stats') loadStats(); }

function fmtTokens(n) {
  if (!n) return '0';
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'k';
  return String(n);
}

async function loadStats() {
  const r = await fetch('/board/stats'); const s = await r.json();
  const v = document.getElementById('view-stats');
  const status = s.by_status || {};
  const statusCards = COLUMNS.map(c => `
    <div class="statcard text-center">
      <div class="stat-num">${status[c]||0}</div>
      <div class="stat-lbl capitalize">${c.replace('_',' ')}</div>
    </div>`).join('') + `
    <div class="statcard text-center">
      <div class="stat-num" style="color:var(--txt-dim)">${status.archived||0}</div>
      <div class="stat-lbl">archived</div>
    </div>`;
  // Tokens shown SEPARATELY — hive and claude never combined.
  const tok = s.tokens || {hive:0, claude:0};
  const tokenPanel = `
    <div class="statcard">
      <div class="stat-lbl mb-1">Hive tokens (Ollama)</div>
      <div class="stat-num tok-hive">${fmtTokens(tok.hive)}</div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Claude tokens (CLI)</div>
      <div class="stat-num tok-claude">${fmtTokens(tok.claude)}</div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Est. cost (claude)</div>
      <div class="stat-num tok-claude">$${(s.cost_usd||0).toFixed(2)}</div>
      <div class="stat-lbl" style="font-size:10px">hive is $0</div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Avg attempts / task</div>
      <div class="stat-num">${s.avg_attempts||0}</div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Smoke gate</div>
      <div class="stat-num"><span class="tok-hive">${(s.smoke||{}).pass||0}✓</span>
        <span style="color:var(--red);font-size:20px"> ${(s.smoke||{}).fail||0}✗</span></div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Lessons learned</div>
      <div class="stat-num">${s.lessons||0}</div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Avg tokens / task</div>
      <div class="stat-num"><span class="tok-hive" style="font-size:20px">${fmtTokens((s.avg_tokens_per_task||{}).hive||0)}H</span>
        <span class="tok-claude" style="font-size:20px"> ${fmtTokens((s.avg_tokens_per_task||{}).claude||0)}C</span></div>
    </div>
    <div class="statcard">
      <div class="stat-lbl mb-1">Parse-fail rate</div>
      <div class="stat-num" style="color:${((s.parse_fail||{}).rate||0) > 0.05 ? 'var(--red)' : 'var(--green)'}">${(((s.parse_fail||{}).rate||0)*100).toFixed(1)}%</div>
      <div class="stat-lbl" style="font-size:11px">${(s.parse_fail||{}).fails||0}/${(s.parse_fail||{}).turns||0} turns</div>
    </div>`;
  const proj = (s.top_projects||[]).map(p => `
    <tr style="border-top:1px solid var(--line)">
      <td class="py-1 pr-3 num text-xs">${p.slug}</td>
      <td class="py-1 px-2 text-right num">${p.done}</td>
      <td class="py-1 px-2 text-right num" style="color:var(--txt-dim)">${p.active}</td>
      <td class="py-1 px-2 text-right num tok-hive">${fmtTokens(p.hive_tokens)}</td>
      <td class="py-1 px-2 text-right num tok-claude">${fmtTokens(p.claude_tokens)}</td>
    </tr>`).join('');
  v.innerHTML = `
    <h2 class="klabel">Pipeline</h2>
    <div class="grid grid-cols-7 gap-3 mb-6">${statusCards}</div>
    <h2 class="klabel">Work + Tokens <span class="note">hive and claude tracked separately, never combined</span></h2>
    <div class="grid grid-cols-4 gap-3 mb-6">${tokenPanel}</div>
    <h2 class="klabel">Top Projects</h2>
    <div class="statcard">
      <table class="w-full text-sm">
        <thead><tr style="color:var(--faint);font-size:11px;letter-spacing:.08em;text-transform:uppercase">
          <th class="text-left pb-1">project</th><th class="px-2 text-right pb-1">done</th>
          <th class="px-2 text-right pb-1">active</th><th class="px-2 text-right pb-1">hive tok</th>
          <th class="px-2 text-right pb-1">claude tok</th></tr></thead>
        <tbody>${proj || '<tr><td class="py-2" style="color:var(--txt-dim)">no data</td></tr>'}</tbody>
      </table>
    </div>
    <h2 class="klabel mt-6">Tokens / Day <span class="note">last 30 days, hive and claude separately</span></h2>
    <div class="statcard" style="padding:12px 16px">
      <canvas id="tokDayCanvas" height="90" style="width:100%;display:block"></canvas>
      <div id="tokDayLegend" class="text-xs mt-1 flex gap-4" style="color:var(--txt-dim)"></div>
    </div>
    <h2 class="klabel mt-6">Lessons Learned</h2>
    <div id="lessonsPanel" class="statcard text-sm" style="color:var(--txt-dim)">loading…</div>`;
  loadLessons();
  loadTokDay();
}
async function loadTokDay() {
  const canvas = document.getElementById('tokDayCanvas');
  const legend = document.getElementById('tokDayLegend');
  if (!canvas) return;
  try {
    const r = await fetch('/board/tokens-by-day?days=30');
    if (!r.ok) return;
    const data = await r.json(); // [{date,hive,claude,total}]
    if (!data.length) { legend.textContent = 'No token data yet.'; return; }
    _drawTokDay(canvas, legend, data);
  } catch(e) { if (legend) legend.textContent = '(unavailable)'; }
}
function _drawTokDay(canvas, legend, data) {
  // Hand-drawn canvas line chart, no external deps.
  // Two series: hive (green) and claude (cyan = telemetry), filled under.
  const W = canvas.offsetWidth || canvas.parentElement.offsetWidth || 600;
  const H = 90;
  canvas.width  = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  const PAD = {l:8, r:8, t:6, b:22};
  const cw = W - PAD.l - PAD.r;
  const ch = H - PAD.t - PAD.b;
  const n = data.length;
  const maxVal = Math.max(1, ...data.map(d => Math.max(d.hive, d.claude)));
  function px(i) { return PAD.l + (i / (n - 1)) * cw; }
  function py(v) { return PAD.t + ch - (v / maxVal) * ch; }
  // Background grid lines
  ctx.strokeStyle = '#3a342c';
  ctx.lineWidth = 1;
  for (let g = 0; g <= 3; g++) {
    const y = PAD.t + (g / 3) * ch;
    ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + cw, y); ctx.stroke();
  }
  // Draw a filled series
  function drawSeries(color, fill, getter) {
    ctx.beginPath();
    ctx.moveTo(px(0), py(getter(data[0])));
    for (let i = 1; i < n; i++) ctx.lineTo(px(i), py(getter(data[i])));
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.lineTo(px(n-1), PAD.t + ch);
    ctx.lineTo(PAD.l, PAD.t + ch);
    ctx.closePath();
    ctx.fillStyle = fill; ctx.fill();
  }
  drawSeries('#5cc870', 'rgba(92,200,112,0.12)', d => d.hive);
  drawSeries('#60c8c8', 'rgba(96,200,200,0.12)', d => d.claude);
  // X-axis date labels (first, mid, last)
  ctx.fillStyle = '#8a8780'; ctx.font = '9px "JetBrains Mono",ui-monospace,monospace'; ctx.textAlign = 'center';
  const labelIdx = [0, Math.floor((n-1)/2), n-1];
  for (const i of labelIdx) ctx.fillText(data[i].date.slice(5), px(i), H - 5);
  // Legend
  const last = data[data.length-1];
  const fmtK = v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'k' : String(v);
  legend.innerHTML =
    '<span class="num" style="color:#5cc870">&#9632; hive '+fmtK(last.hive)+' (latest day)</span>' +
    '<span class="num" style="color:#60c8c8">&#9632; claude '+fmtK(last.claude)+' (latest day)</span>';
}
async function loadLessons() {
  const el = document.getElementById('lessonsPanel');
  if (!el) return;
  try {
    const r = await fetch('/board/lessons'); const ls = await r.json();
    if (!ls.length) { el.textContent = 'No lessons yet; they accrue when claude rescues a task.'; return; }
    el.innerHTML = ls.map(l => `<div style="padding:4px 0;border-top:1px solid var(--line)">
      <span class="chip" style="background:var(--card-hi);color:var(--txt-dim)">${escapeHtml(l.project)}</span>
      <span style="color:var(--txt)"> ${escapeHtml(l.body)}</span></div>`).join('');
  } catch(e){ el.textContent = '(lessons unavailable)'; }
}

async function loadState() {
  const r = await fetch('/board/state'); const j = await r.json();
  STATE.tasks = j.tasks; STATE.projects = j.projects; STATE.pending_approvals = j.pending_approvals;
  _applyPaused(!!j.paused);
  render();
}
function _applyPaused(paused) {
  BOARD_PAUSED = paused;
  const btn = document.getElementById('pauseBtn');
  const banner = document.getElementById('pauseBanner');
  if (paused) {
    if (btn) { btn.textContent = '▶ Resume'; btn.style.background='oklch(0.83 0.15 78 / 0.12)'; btn.style.color='var(--accent)'; btn.style.borderColor='var(--accent)'; }
    if (banner) banner.classList.remove('hidden');
  } else {
    if (btn) { btn.textContent = '⏸ Pause'; btn.style.background=''; btn.style.color=''; btn.style.borderColor=''; }
    if (banner) banner.classList.add('hidden');
  }
}
async function togglePause() {
  const url = BOARD_PAUSED ? '/board/resume' : '/board/pause';
  try {
    const r = await fetch(url, {method:'POST',headers:_mutHeaders()});
    if (!r.ok) { alert('pause/resume failed: ' + (await r.text())); return; }
    const d = await r.json();
    _applyPaused(!!d.paused);
  } catch(e) { alert('pause/resume error: '+e); }
}
function _visible(t) {
  if (FILTER_PROJ && t.project_slug !== FILTER_PROJ) return false;
  if (FILTER_Q && !(`${t.slug} ${t.title} ${t.project_slug}`.toLowerCase().includes(FILTER_Q))) return false;
  return true;
}
function _syncProjFilter() {
  const sel = document.getElementById('projFilter');
  if (!sel) return;
  const slugs = [...new Set(STATE.tasks.map(t => t.project_slug))].sort();
  const cur = FILTER_PROJ;
  sel.innerHTML = `<option value="">All projects</option>` +
    slugs.map(s => `<option value="${escapeHtml(s)}" ${s===cur?'selected':''}>${escapeHtml(s)}</option>`).join('');
}
function _nowBuilding() {
  const el = document.getElementById('nowBuilding');
  const ip = STATE.tasks.filter(t => t.status === 'in_progress');
  if (!ip.length) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  el.innerHTML = ip.map(t => {
    const since = t.updated_at ? _ago(t.updated_at) : '';
    return `<div class="flex items-center gap-2 text-sm" style="cursor:pointer" onclick='openDetail("${t.slug}")'>
      <span class="livedot" style="background:var(--copper);box-shadow:0 0 6px var(--copper)"></span>
      <span style="color:var(--copper);font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:11px">building</span>
      <span style="color:var(--txt)">${escapeHtml(t.title)}</span>
      <span style="color:var(--txt-dim)" class="text-xs num">${t.project_slug} · ${t.agent_turns||0} turns · ${since}</span>
      <span class="liveact" id="nblive-${t.slug}" style="margin:0">${t.last_action?'<span class="livedot"></span>'+escapeHtml(t.last_action):''}</span>
    </div>`;
  }).join('');
}
function _ago(iso) {
  try {
    const d = new Date(iso.replace(' ','T') + (iso.endsWith('Z')?'':'Z'));
    const s = Math.max(0, (Date.now()-d.getTime())/1000);
    if (s<60) return Math.round(s)+'s'; if (s<3600) return Math.round(s/60)+'m';
    return Math.round(s/3600)+'h';
  } catch(e){ return ''; }
}
function render() {
  _syncProjFilter();
  _nowBuilding();
  const cont = document.getElementById('view-board');
  cont.innerHTML = '';
  for (const col of COLUMNS) {
    const tasks = STATE.tasks.filter(t => t.status === col && _visible(t));
    const div = document.createElement('div');
    div.className = 'col p-2';
    div.innerHTML = `
      <div class="flex items-center justify-between mb-2 px-1" style="border-bottom:1px solid var(--line);padding-bottom:6px">
        <div style="font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)">${col.replace('_',' ')}</div>
        <div class="text-xs num" style="color:var(--faint)">${tasks.length}</div>
      </div>
      <div class="space-y-2">
        ${tasks.map(t => taskCard(t)).join('')}
      </div>
    `;
    cont.appendChild(div);
  }
  const pendCount = STATE.tasks.filter(t => t.status === 'proposed' || t.status === 'review').length + STATE.pending_approvals.length;
  const badge = document.getElementById('badge');
  if (pendCount > 0) {
    badge.textContent = `${pendCount} pending`;
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }
}
function taskCard(t) {
  const prio = {
    high:'background:oklch(0.66 0.17 25 / 0.16);color:var(--red)',
    medium:'background:var(--card-hi);color:var(--txt-dim)',
    low:'background:var(--card-hi);color:var(--faint)'
  }[t.priority] || '';
  const assignee = t.assignee !== 'none' ? `<span class="chip" style="background:oklch(0.74 0.13 56 / 0.16);color:var(--copper)">${t.assignee}</span>` : '';
  const proj = `<span class="text-xs num" style="color:var(--txt-dim)">${t.project_slug}</span>`;
  const checked = (t.acceptance_criteria || []).filter(c=>c.checked).length;
  const total = (t.acceptance_criteria || []).length;
  const progress = total ? `<span class="text-xs num" style="color:var(--txt-dim)">${checked}/${total}</span>` : '';
  const htok = t.hive_tokens ? `<span class="chip num tok-hive" style="background:oklch(0.80 0.16 150 / 0.12)" title="hive tokens">H ${fmtTokens(t.hive_tokens)}</span>` : '';
  const ctok = t.claude_tokens ? `<span class="chip num tok-claude" style="background:oklch(0.80 0.10 200 / 0.12)" title="claude tokens">C ${fmtTokens(t.claude_tokens)}</span>` : '';
  // Live "now doing" line — only while in_progress, so you can watch
  // the hive work turn by turn.
  const live = (t.status === 'in_progress' && t.last_action)
    ? `<div class="liveact" id="live-${t.slug}"><span class="livedot"></span>${escapeHtml(t.last_action)}</div>`
    : '';
  const rate = (t.status === 'in_progress')
    ? `<span class="chip num" style="background:var(--card-hi);color:var(--txt-dim)" title="turns, elapsed">${t.agent_turns||0}t · ${t.updated_at?_ago(t.updated_at):''}</span>`
    : '';
  return `<div class="card p-2 cursor-pointer" onclick='openDetail("${t.slug}")'>
    <div class="flex items-start justify-between gap-2 mb-1">
      <div class="text-sm font-medium" style="color:var(--txt)">${escapeHtml(t.title)}</div>
      <div class="flex items-center gap-1">
        <span class="chip num" style="background:var(--card-hi);color:var(--faint)">${t.slug}</span>
        <button onclick="event.stopPropagation();deleteTask('${t.slug}')" title="Delete permanently" style="color:var(--faint);background:none;border:none;cursor:pointer;padding:0 2px;font-size:13px;line-height:1" onmouseover="this.style.color='var(--red)'" onmouseout="this.style.color='var(--faint)'">🗑</button>
      </div>
    </div>
    <div class="flex items-center gap-1.5 flex-wrap">
      ${proj}
      <span class="chip" style="${prio}">${t.priority}</span>
      ${assignee}
      ${progress}
      ${htok}${ctok}
      ${t.smoke_cmd?`<span class="chip" style="${t.smoke_ok===true?'background:oklch(0.80 0.16 150 / 0.12);color:var(--green)':t.smoke_ok===false?'background:oklch(0.66 0.17 25 / 0.16);color:var(--red)':'background:var(--card-hi);color:var(--txt-dim)'}" title="smoke gate">⚙${t.smoke_ok===true?'✓':t.smoke_ok===false?'✗':''}</span>`:''}
      ${t.review_by?`<span class="chip" style="background:oklch(0.83 0.15 78 / 0.12);color:var(--accent)" title="reviewer">👁</span>`:''}
      ${rate}
    </div>
    ${live}
  </div>`;
}
function escapeHtml(s) {
  return s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
}
function openDetail(slug) {
  const t = STATE.tasks.find(x => x.slug === slug);
  if (!t) return;
  const dlg = document.getElementById('dlg');
  const live = (t.status==='in_progress' && t.last_action)
    ? `<div class="liveact mb-2"><span class="livedot"></span>${escapeHtml(t.last_action)}</div>` : '';
  dlg.innerHTML = `
    <div class="p-5" style="background:var(--panel);color:var(--txt)">
      <div class="flex items-start justify-between gap-2">
        <h2 class="text-lg font-semibold" style="color:var(--txt)">${escapeHtml(t.title)} <span class="text-sm" style="color:var(--txt-dim)">${t.slug}</span></h2>
        <div class="flex items-center gap-2">
          ${(t.status!=='done'&&t.status!=='archived')?`<button onclick="unstuckTask('${t.slug}')" title="Bring Claude in to diagnose and push this ticket along" class="text-xs rounded px-2 py-0.5" style="border:1px solid oklch(0.80 0.10 200 / 0.45);background:oklch(0.80 0.10 200 / 0.12);color:var(--cyan)">🩹 Unstuck</button>`:''}
          <button onclick="document.getElementById('dlg').close();deleteTask('${t.slug}')" title="Delete permanently" class="text-xs rounded px-2 py-0.5" style="border:1px solid oklch(0.66 0.17 25 / 0.4);background:oklch(0.66 0.17 25 / 0.12);color:var(--red)">🗑 Delete</button>
          <button onclick="document.getElementById('dlg').close()" style="color:var(--txt-dim)">✕</button>
        </div>
      </div>
      <div class="text-sm mb-2" style="color:var(--txt-dim)">${t.project_slug} · status ${t.status} · assignee ${t.assignee}${t.attempt_count?` · attempt ${t.attempt_count}`:''}</div>
      ${live}
      <div class="flex flex-wrap gap-1 mb-2">
        ${t.review_by?`<span class="chip" style="background:oklch(0.83 0.15 78 / 0.12);color:var(--accent)">review: ${escapeHtml(t.review_by)}</span>`:''}
        ${t.polish_iters?`<span class="chip num" style="background:oklch(0.83 0.15 78 / 0.12);color:var(--accent)">polish ×${t.polish_iters}</span>`:''}
        ${t.smoke_cmd?`<span class="chip" style="${t.smoke_ok===true?'background:oklch(0.80 0.16 150 / 0.12);color:var(--green)':t.smoke_ok===false?'background:oklch(0.66 0.17 25 / 0.16);color:var(--red)':'background:var(--card-hi);color:var(--txt-dim)'}">smoke ${t.smoke_ok===true?'✓':t.smoke_ok===false?'✗':'·'}</span>`:''}
        ${(t.depends_on||[]).length?`<span class="chip num" style="background:var(--card-hi);color:var(--txt-dim)">deps: ${t.depends_on.length}</span>`:''}
        ${t.hive_tokens?`<span class="chip num tok-hive" style="background:oklch(0.80 0.16 150 / 0.12)">hive ${fmtTokens(t.hive_tokens)} tok</span>`:''}
        ${t.claude_tokens?`<span class="chip num tok-claude" style="background:oklch(0.80 0.10 200 / 0.12)">claude ${fmtTokens(t.claude_tokens)} tok</span>`:''}
      </div>
      <pre class="text-sm whitespace-pre-wrap p-2 rounded max-h-40 overflow-auto" style="background:oklch(0.12 0.012 58);color:var(--txt);border:1px solid var(--line)">${escapeHtml(t.body || '(no body)')}</pre>
      <h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Acceptance criteria</h3>
      <ul class="space-y-1 text-sm" style="color:var(--txt)">
        ${(t.acceptance_criteria || []).map((c,i) => `
          <li><label class="flex items-start gap-2"><input type="checkbox" ${c.checked?'checked':''} onchange="toggleCriterion('${t.slug}',${i},this.checked)" /><span>${escapeHtml(c.text)}</span></label></li>
        `).join('')}
      </ul>
      ${(t.files_of_interest || []).length ? `<h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Files</h3><ul class="text-xs" style="color:var(--txt-dim)">${(t.files_of_interest).map(f=>`<li><code>${escapeHtml(f)}</code></li>`).join('')}</ul>` : ''}
      ${Object.keys(t.verify_results||{}).length ? `<h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Verify</h3><pre class="text-xs p-2 rounded max-h-32 overflow-auto" style="background:oklch(0.12 0.012 58);color:var(--txt);border:1px solid var(--line)">${escapeHtml(JSON.stringify(t.verify_results, null, 2))}</pre>` : ''}
      <h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Transcript <span class="text-xs" style="color:var(--txt-dim)">(agent turns)</span></h3>
      <div id="transcript" class="text-xs p-2 rounded max-h-48 overflow-auto num" style="background:oklch(0.12 0.012 58);border:1px solid var(--line);color:var(--txt-dim)">loading…</div>
      <h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Diff <span class="text-xs" style="color:var(--txt-dim)">(this task's commit)</span></h3>
      <pre id="diff" class="text-xs p-2 rounded max-h-56 overflow-auto" style="background:oklch(0.12 0.012 58);border:1px solid var(--line);color:var(--txt-dim);white-space:pre-wrap">loading…</pre>
      ${t.status==='review'?`<div class="mt-3 flex gap-2">
        <button onclick="moveTask('${t.slug}','done')" class="rounded px-3 py-1" style="background:oklch(0.80 0.16 150 / 0.14);color:var(--green);border:1px solid oklch(0.80 0.16 150 / 0.4)">✓ Approve, done</button>
        <button onclick="moveTask('${t.slug}','in_progress')" class="rounded px-3 py-1" style="background:oklch(0.66 0.17 25 / 0.14);color:var(--red);border:1px solid oklch(0.66 0.17 25 / 0.4)">✗ Reject, rework</button>
      </div>`:''}
      <div class="mt-4 flex flex-wrap gap-1">
        ${COLUMNS.map(c => `<button onclick="moveTask('${t.slug}','${c}')" class="text-xs rounded px-2 py-0.5" style="border:1px solid var(--line);${c===t.status?'background:var(--accent);color:var(--on-amber)':'background:var(--card);color:var(--txt)'}">${c.replace('_',' ')}</button>`).join('')}
      </div>
      <div class="mt-3 flex gap-2 items-center text-sm" style="color:var(--txt)">
        <label>Assignee:</label>
        <select onchange="assign('${t.slug}',this.value)" class="rounded px-2 py-1 text-sm" style="background:var(--card);color:var(--txt);border:1px solid var(--line)">
          ${['none','hive','claude-code','owner'].map(a => `<option value="${a}" ${a===t.assignee?'selected':''}>${a}</option>`).join('')}
        </select>
      </div>
    </div>
  `;
  dlg.showModal();
  loadTranscript(slug);
  loadDiff(slug);
}
async function loadDiff(slug) {
  const el = document.getElementById('diff');
  if (!el) return;
  try {
    const r = await fetch(`/board/tasks/${slug}/diff`);
    const d = await r.json();
    if (!d.diff) { el.textContent = d.note || '(no diff)'; return; }
    el.textContent = (d.sha?`commit ${d.sha}\n`:'') + d.diff;
  } catch(e){ el.textContent = '(diff unavailable)'; }
}
async function loadTranscript(slug) {
  const el = document.getElementById('transcript');
  if (!el) return;
  try {
    const r = await fetch(`/board/tasks/${slug}/transcript`);
    const turns = await r.json();
    if (!turns.length) { el.textContent = '(no transcript yet)'; return; }
    el.innerHTML = turns.map(t => {
      const dot = t.ok === true ? 'var(--green)' : t.ok === false ? 'var(--red)' : 'var(--txt-dim)';
      return `<div style="display:flex;gap:6px;padding:1px 0">
        <span style="color:var(--txt-dim);width:36px">t${t.turn ?? ''}</span>
        <span style="color:${dot}">●</span>
        <span style="color:var(--txt)">${escapeHtml(t.label || '')}</span></div>`;
    }).join('');
  } catch (e) { el.textContent = '(transcript unavailable)'; }
}
function openCreate() {
  const dlg = document.getElementById('dlg');
  dlg.innerHTML = `
    <form class="p-5 space-y-2" style="background:var(--panel);color:var(--txt)" onsubmit="event.preventDefault();createTask()">
      <h2 class="text-lg font-semibold" style="color:var(--txt)">New task</h2>
      <input id="t_title" class="w-full rounded px-2 py-1" style="background:var(--card);color:var(--txt);border:1px solid var(--line)" placeholder="Title" required />
      <select id="t_project" class="w-full rounded px-2 py-1" style="background:var(--card);color:var(--txt);border:1px solid var(--line)">
        ${STATE.projects.map(p => `<option value="${escapeHtml(p.slug)}">${escapeHtml(p.name)}${p.enabled?'':' (disabled)'}</option>`).join('')}
      </select>
      <textarea id="t_body" class="w-full rounded px-2 py-1 h-24" style="background:var(--card);color:var(--txt);border:1px solid var(--line)" placeholder="Body / description"></textarea>
      <textarea id="t_criteria" class="w-full rounded px-2 py-1 h-20" style="background:var(--card);color:var(--txt);border:1px solid var(--line)" placeholder="Acceptance criteria, one per line"></textarea>
      <select id="t_priority" class="rounded px-2 py-1" style="background:var(--card);color:var(--txt);border:1px solid var(--line)">
        <option value="low">low</option><option value="medium" selected>medium</option><option value="high">high</option>
      </select>
      <div class="flex gap-2 justify-end">
        <button type="button" onclick="document.getElementById('dlg').close()" class="rounded px-3 py-1" style="border:1px solid var(--line);background:var(--card);color:var(--txt)">Cancel</button>
        <button class="rounded px-3 py-1" style="background:var(--accent);color:var(--on-amber);font-weight:600">Create</button>
      </div>
    </form>
  `;
  dlg.showModal();
}
async function createTask() {
  const criteriaLines = document.getElementById('t_criteria').value.split('\\n').map(s=>s.trim()).filter(Boolean);
  const r = await fetch('/board/tasks', {
    method:'POST', headers:_mutHeaders(),
    body: JSON.stringify({
      title: document.getElementById('t_title').value,
      body: document.getElementById('t_body').value,
      project_slug: document.getElementById('t_project').value,
      priority: document.getElementById('t_priority').value,
      acceptance_criteria: criteriaLines.map(text => ({text, checked:false})),
    }),
  });
  if (r.ok) { document.getElementById('dlg').close(); await loadState(); }
  else alert(await r.text());
}
async function moveTask(slug, status) {
  const r = await fetch(`/board/tasks/${slug}/move`, {
    method:'POST', headers:_mutHeaders(),
    body: JSON.stringify({status}),
  });
  if (r.ok) { await loadState(); openDetail(slug); }
  else alert(await r.text());
}
async function assign(slug, assignee) {
  await fetch(`/board/tasks/${slug}/assign`, {
    method:'POST', headers:_mutHeaders(),
    body: JSON.stringify({assignee}),
  });
  await loadState();
}
async function unstuckTask(slug) {
  if (!confirm(`Bring Claude in to unstick ${slug}?\n\nClaude will read the project, diagnose why it stalled, and either fix it or explain why it can't be done as specced. This may take a few minutes; the ticket moves to IN PROGRESS while it works, then to REVIEW with Claude's summary.`)) return;
  try {
    const r = await fetch(`/board/tasks/${slug}/unstuck`, {method:'POST', headers:_mutHeaders()});
    if (!r.ok) { alert('Unstuck failed: ' + (await r.text())); return; }
    document.getElementById('dlg').close();
    await loadState();
  } catch(e) { alert('Unstuck error: ' + e); }
}
async function deleteTask(slug) {
  if (!confirm(`Delete ${slug} permanently? This cannot be undone.`)) return;
  try {
    const r = await fetch(`/board/tasks/${slug}/delete`, {
      method:'POST', headers:_mutHeaders(),
    });
    if (!r.ok) { alert('Delete failed: ' + (await r.text())); return; }
    await loadState();
  } catch(e) { alert('Delete error: ' + e); }
}
async function toggleCriterion(slug, idx, checked) {
  const t = STATE.tasks.find(x => x.slug === slug);
  const next = [...(t.acceptance_criteria||[])];
  next[idx] = {...next[idx], checked};
  await fetch(`/board/tasks/${slug}/criteria`, {
    method:'POST', headers:_mutHeaders(),
    body: JSON.stringify({acceptance_criteria: next}),
  });
  await loadState();
}
function openProjects() {
  const dlg = document.getElementById('dlg');
  dlg.innerHTML = `
    <div class="p-5" style="background:var(--panel);color:var(--txt)">
      <div class="flex items-start justify-between gap-2 mb-3">
        <h2 class="text-lg font-semibold" style="color:var(--txt)">Projects</h2>
        <button onclick="document.getElementById('dlg').close()" style="color:var(--txt-dim)">✕</button>
      </div>
      <ul class="space-y-1 text-sm">
        ${STATE.projects.map(p => `
          <li class="rounded p-2 flex items-center justify-between" style="border:1px solid var(--line);background:var(--card)">
            <div><div class="font-medium" style="color:var(--txt)">${escapeHtml(p.name)}${p.enabled?' <span class="chip" style="background:oklch(0.80 0.16 150 / 0.14);color:var(--green)">on</span>':''}</div><div class="text-xs" style="color:var(--txt-dim)">${escapeHtml(p.path)}</div></div>
            <div class="flex gap-1">
              <button class="text-xs rounded px-2 py-0.5" style="border:1px solid var(--line);background:var(--card-hi);color:var(--txt)" onclick="toggleProject('${p.slug}',${!p.enabled})">${p.enabled?'disable':'enable'}</button>
            </div>
          </li>
        `).join('')}
      </ul>
    </div>
  `;
  dlg.showModal();
}
async function toggleProject(slug, enabled) {
  await fetch(`/board/projects/${slug}/${enabled?'enable':'disable'}`, {method:'POST',headers:_mutHeaders()});
  await loadState();
  openProjects();
}
function connectEvents() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  SOCK = new WebSocket(`${proto}://${location.host}/board/events`);
  SOCK.onmessage = (ev) => {
    try {
      const m = JSON.parse(ev.data);
      if (m && m.event === 'task_progress' && m.task) {
        // Every agent turn — update live lines in place (card + header),
        // no full reload.
        const html = '<span class="livedot"></span>' +
          (m.action || '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
        const c = document.getElementById('live-' + m.task);
        const h = document.getElementById('nblive-' + m.task);
        if (c) c.innerHTML = html;
        if (h) h.innerHTML = html;
        if (c || h) return;
      }
      // Owner-action events → optional browser notification.
      if (NOTIFY && m && ['review_ready','escalated','review_rejected','max_attempts','qa_failed'].includes(m.event)) {
        _notify(m.event, m.task);
      }
    } catch (e) {}
    loadState();
  };
  SOCK.onclose = () => setTimeout(connectEvents, 3000);
}
function _notify(event, task) {
  const labels = {review_ready:'Ready for review', escalated:'Escalated to Claude',
    review_rejected:'Review rejected', max_attempts:'Max attempts hit',
    qa_passed:'QA passed', qa_failed:'QA failed'};
  const body = `${labels[event]||event}: ${task||''}`;
  try {
    if (Notification.permission === 'granted') new Notification('Crew Board', {body});
    else if (Notification.permission !== 'denied') Notification.requestPermission();
  } catch(e){}
  try { new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=').play(); } catch(e){}
}
async function selfImprove() {
  if (!confirm('Mine the board for failure patterns and create proposed improvement tickets on ai-team?')) return;
  try {
    const r = await fetch('/board/self-improve', {method:'POST',headers:_mutHeaders()});
    const d = await r.json();
    alert(`Self-improve: found ${d.found} patterns, created ${d.created.length} proposed tickets` + (d.created.length?` (${d.created.join(', ')})`:''));
    await loadState();
  } catch(e){ alert('self-improve failed: '+e); }
}
function openGoal() {
  const dlg = document.getElementById('dlg');
  dlg.innerHTML = `
    <form class="p-5 space-y-3" style="background:var(--panel);color:var(--txt);width:560px;max-width:100%" onsubmit="event.preventDefault();decomposeGoal()">
      <h2 class="text-lg font-semibold" style="color:var(--txt)">✦ Describe a goal</h2>
      <div class="text-xs" style="color:var(--txt-dim)">An LLM breaks it into a chained ticket plan you can review before it builds.</div>
      <textarea id="g_goal" class="w-full rounded px-2 py-1 h-24" style="background:var(--card);color:var(--txt);border:1px solid var(--line)" placeholder="e.g. Build a Tetris game for Android"></textarea>
      <select id="g_project" class="w-full rounded px-2 py-1" style="background:var(--card);color:var(--txt);border:1px solid var(--line)">
        <option value="">(new project — auto-named)</option>
        ${STATE.projects.filter(p=>p.enabled).map(p => `<option value="${p.slug}">${p.slug}</option>`).join('')}
      </select>
      <div id="g_plan" class="text-xs" style="color:var(--txt-dim)"></div>
      <div class="flex gap-2 justify-end">
        <button type="button" onclick="document.getElementById('dlg').close()" class="rounded px-3 py-1" style="border:1px solid var(--line);background:var(--card);color:var(--txt)">Cancel</button>
        <button id="g_btn" class="rounded px-3 py-1" style="background:var(--accent);color:var(--on-amber);font-weight:600">Plan it</button>
      </div>
    </form>`;
  dlg.showModal();
}
async function decomposeGoal() {
  const goal = document.getElementById('g_goal').value.trim();
  const project = document.getElementById('g_project').value;
  const plan = document.getElementById('g_plan');
  const btn = document.getElementById('g_btn');
  if (!goal) return;
  btn.disabled = true; plan.textContent = 'Planning…';
  try {
    const r = await fetch('/board/decompose', {method:'POST',headers:_mutHeaders(),
      body: JSON.stringify({goal, project_slug: project})});
    const d = await r.json();
    if (!r.ok) { plan.textContent = 'Error: ' + (d.detail||JSON.stringify(d)); btn.disabled=false; return; }
    plan.innerHTML = `<div style="color:var(--accent)">Created ${d.created} tickets on '${d.project_slug}'${d.scaffolded?' (new project)':''}:</div>` +
      (d.titles||[]).map((t,i)=>`<div>${i+1}. ${escapeHtml(t)}</div>`).join('');
    btn.textContent = 'Done'; await loadState();
  } catch(e){ plan.textContent = 'Failed: '+e; btn.disabled=false; }
}
loadState();
connectEvents();
</script>
</body></html>
"""
