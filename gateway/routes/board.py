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
import re
import secrets
import time
from pathlib import Path

# SECURITY (audit M1): project_slug is rendered into the board DOM; constrain it
# to a clean slug shape on the way in so an HTML/script payload can't be planted.
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

# SECURITY: greenfield project creation (and the virtual 'content' project) are
# confined to this root. Configurable via HIVE_PROJECTS_ROOT (docs/CONFIG.md);
# defaults to ~/projects. This MUST stay a real filesystem boundary — the
# /projects/create guard below re-validates every path against it.
_PROJECTS_ROOT = Path(os.environ.get(
    "HIVE_PROJECTS_ROOT", str(Path.home() / "projects"),
))

# Stats payload is cached briefly so repeated Stats-tab polls don't
# re-scan every task + up to 50 transcript files each refresh.
_STATS_TTL_S = 15.0

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from gateway.crew_board import schema
from gateway.crew_board.store import Board, CrewBoardStore, Project, Task
# SECURITY (audit H2): board read routes that expose task bodies, verify_results
# (test stderr tails), source diffs, and absolute project paths must not be open
# to the tailnet. This dep allows loopback (the dashboard) + valid device Bearer,
# and 401s anonymous tailnet callers.
from gateway.deps import require_device_or_loopback

# Lazy imports for P7 board surfacing — only used in /board/stats.
_BENCH_RESULTS_PATH = Path("state/bench_results.json")
_LOOP_DECISIONS_PATH = Path(__file__).resolve().parents[2] / "bench" / "loop_decisions.json"

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
        # #198: last-agent handoff summary ("what I did + state + next step")
        # + who wrote it (model/agent) and when — shown in the detail drawer
        # and as a one-line note on the card.
        "last_summary": getattr(t, "last_summary", None),
        "last_summary_by": getattr(t, "last_summary_by", None),
        "last_summary_at": getattr(t, "last_summary_at", None),
        # CP1: live agent reasoning stream for the in-ticket thoughts panel.
        "live_thoughts": getattr(t, "live_thoughts", []) or [],
        # CP2: master-plan spec for kind='plan' tickets (the proposed gate).
        "plan_spec": getattr(t, "plan_spec", {}) or {},
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
        "modified_at": getattr(p, "modified_at", None),
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
    type: str = Field("image", pattern="^(image|video|avatar)$")
    prompt: str = Field(..., min_length=1, max_length=2000)
    count: int = Field(1, ge=1, le=4)
    width: int = Field(1024, ge=64, le=2048)
    height: int = Field(1024, ge=64, le=2048)
    negative_prompt: str = ""
    seed_media_id: str | None = None    # required for video (image→video)
    # avatar: prompt is the spoken script; the face is an optional image media id.
    image_media_id: str | None = None
    voice: str = "af_heart"
    avatar_name: str = "ai_woman"
    preprocess: str = Field("crop", pattern="^(crop|resize|full)$")
    still: bool = False
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
    if body.type == "avatar":
        spec["voice"] = body.voice
        spec["avatar_name"] = body.avatar_name
        spec["preprocess"] = body.preprocess
        spec["still"] = body.still
        if body.image_media_id:
            spec["image_media_id"] = body.image_media_id
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


def _board_to_dict(b: Board) -> dict:
    return {
        "board_id": b.board_id,
        "name": b.name,
        "description": b.description,
        "created_at": b.created_at,
    }


@router.get("/list")
async def list_boards(request: Request) -> JSONResponse:
    """P2 v-Next: List all registered boards. Open read endpoint.
    Always includes at least the 'default' board."""
    store = _store(request)
    boards = store.list_boards()
    return JSONResponse([_board_to_dict(b) for b in boards])


@router.post("/boards")
async def create_board(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """P2 v-Next: Create a new board. Requires board auth.
    Body: {board_id: str, name: str, description: str}"""
    store = _store(request)
    board_id = str(payload.get("board_id", "")).strip()
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "")).strip()
    if not board_id or not name:
        raise HTTPException(400, "board_id and name required")
    try:
        board = store.create_board(board_id, name, description)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return JSONResponse(_board_to_dict(board))


@router.get("/state")
async def get_state(
    request: Request,
    board: str | None = Query(default=None),
    _auth: object = Depends(require_device_or_loopback),
) -> JSONResponse:
    """JSON snapshot for client refresh.

    P2 v-Next: when ``?board=<board_id>`` is provided, only tasks belonging
    to that board are returned. When omitted (back-compat), ALL tasks are
    returned exactly as before.
    """
    store = _store(request)
    tasks = store.list_tasks(board_id=board)
    projects = store.list_projects()
    approvals = store.list_pending_approvals()
    return JSONResponse({
        "tasks": [_task_to_dict(t) for t in tasks],
        "projects": [_project_to_dict(p) for p in projects],
        "pending_approvals": approvals,
        "paused": store.is_paused(),
        # Per-lane (board column) model override. in_progress = the build model.
        "lane_models": {"in_progress": store.get_meta("lane_model:in_progress") or ""},
    })


# Board statuses that can carry a per-lane model override (in_progress is the
# only one that actually runs a model today — the build; the rest are accepted
# for forward-compat).
_LANE_STATUSES = {"proposed", "backlog", "ready", "in_progress", "qa", "review", "done"}


@router.get("/models")
async def list_ollama_models(
    request: Request,
    _auth: object = Depends(require_device_or_loopback),
) -> JSONResponse:
    """Ollama's installed model names, for the per-lane model picker."""
    import os
    import httpx
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    if not host.startswith("http"):
        host = "http://" + host
    names: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{host}/api/tags")
            if r.status_code == 200:
                names = sorted({
                    str(m.get("name", "")) for m in r.json().get("models", [])
                    if m.get("name")
                })
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse({"models": names})


@router.post("/lane-model")
async def set_lane_model(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Set the model the hive uses for a board lane. in_progress = the build
    model the agent loop runs with. Empty model clears the override (default)."""
    store = _store(request)
    status = str(payload.get("status", "")).strip()
    model = str(payload.get("model", "")).strip()
    if status not in _LANE_STATUSES:
        raise HTTPException(400, f"unknown lane {status!r}")
    store.set_meta(f"lane_model:{status}", model)
    return JSONResponse({"status": status, "model": model})


@router.post("/tasks/{slug}/steer")
async def steer_task(
    slug: str,
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """CP1: queue an owner steer nudge for a running task. The hive loop injects
    it on its next turn (one-shot), so you can redirect the AI mid-build."""
    store = _store(request)
    msg = str(payload.get("message", "")).strip()
    if not msg:
        raise HTTPException(400, "empty steer message")
    store.set_steer(slug, msg)
    return JSONResponse({"ok": True, "slug": slug})


def _plan_body(spec: dict) -> str:
    """Render a master-plan spec as readable text for the ticket body."""
    lines = [f"GOAL: {spec.get('goal', '')}", ""]
    if spec.get("assumptions"):
        lines.append("Assumptions:")
        lines += [f"- {a}" for a in spec["assumptions"]]
        lines.append("")
    if spec.get("open_questions"):
        lines.append("Open questions:")
        lines += [f"- {q}" for q in spec["open_questions"]]
        lines.append("")
    lines.append("Plan (checkpoints):")
    for i, s in enumerate(spec.get("steps", []), 1):
        lines.append(f"{i}. {s.get('title', '')}")
        if s.get("verify"):
            lines.append(f"   verify: {s['verify']}")
    return "\n".join(lines)


@router.post("/plans/propose")
async def propose_plan(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """CP2: draft a Karpathy master plan for a goal and park it in proposed for
    approval (instead of dumping tickets straight into the queue)."""
    store = _store(request)
    slug = str(payload.get("project_slug", "")).strip()
    goal = str(payload.get("goal", "")).strip()
    if not slug or not goal:
        raise HTTPException(400, "project_slug + goal required")
    from gateway.crew_board.master_plan import draft_plan
    spec = await draft_plan(store, slug, goal)
    if not spec.get("steps"):
        raise HTTPException(502, "plan drafting produced no steps")
    t = store.create_task(
        title=f"[plan] {spec['goal'][:90]}",
        project_slug=slug, body=_plan_body(spec),
        created_by="planner", kind="plan", tags=["plan"],
    )
    store.set_plan_spec(t.slug, spec)
    return JSONResponse({"slug": t.slug, "steps": len(spec["steps"]), "spec": spec})


@router.post("/plans/{slug}/approve")
async def approve_plan(
    slug: str,
    request: Request,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """CP2: approve a master plan — break it out into one child ticket per step
    (acceptance criteria = the step's check-offs), then archive the plan."""
    store = _store(request)
    t = store.get_task(slug)
    if t is None or getattr(t, "kind", "") != "plan":
        raise HTTPException(404, f"no plan {slug!r}")
    spec = getattr(t, "plan_spec", {}) or {}
    created: list[str] = []
    for s in spec.get("steps", []):
        crit = [{"text": c, "checked": False} for c in (s.get("criteria") or [])][:5]
        body = s.get("why", "")
        if s.get("verify"):
            body = (body + f"\n\nVerify: {s['verify']}").strip()
        ch = store.create_task(
            title=s["title"], project_slug=t.project_slug, body=body,
            created_by="owner", acceptance_criteria=crit,
            tags=[f"from-plan:{slug}"],
        )
        created.append(ch.slug)
    store.move_task(slug, schema.STATUS_ARCHIVED, actor="owner",
                    detail=f"plan approved -> {len(created)} tickets")
    return JSONResponse({"approved": slug, "created": created})


@router.post("/plans/{slug}/reject")
async def reject_plan(
    slug: str,
    request: Request,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """CP2: reject a master plan — archive it, no tickets created."""
    store = _store(request)
    store.move_task(slug, schema.STATUS_ARCHIVED, actor="owner",
                    detail="plan rejected")
    return JSONResponse({"rejected": slug})


@router.post("/plans/{slug}/request-changes")
async def request_changes_plan(
    slug: str,
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """CP2: re-draft a master plan from the owner's feedback; stays in proposed."""
    store = _store(request)
    t = store.get_task(slug)
    if t is None or getattr(t, "kind", "") != "plan":
        raise HTTPException(404, f"no plan {slug!r}")
    feedback = str(payload.get("feedback", "")).strip()
    if not feedback:
        raise HTTPException(400, "feedback required")
    spec = getattr(t, "plan_spec", {}) or {}
    from gateway.crew_board.master_plan import draft_plan
    new_spec = await draft_plan(
        store, t.project_slug, spec.get("goal", t.title), feedback=feedback)
    if not new_spec.get("steps"):
        raise HTTPException(502, "re-draft produced no steps")
    store.set_plan_spec(slug, new_spec)
    return JSONResponse({"slug": slug, "steps": len(new_spec["steps"]), "spec": new_spec})


@router.post("/tasks/{slug}/suggest-skills")
async def suggest_skills_route(
    slug: str,
    request: Request,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """#210: analyze a finished task and propose skill improvements (a new skill
    or an update to an existing one) as tickets in the Proposed lane."""
    store = _store(request)
    t = store.get_task(slug)
    if t is None:
        raise HTTPException(404, f"no task {slug!r}")
    from gateway.crew_board.skills_suggest import suggest_skills
    sugg = await suggest_skills(store, t)
    created: list[str] = []
    for s in sugg:
        ch = store.create_task(
            title=f"[skill·{s['kind']}] {s['skill']}",
            project_slug=t.project_slug, body=s["why"],
            created_by="planner", tags=["skill", "from-review", s["kind"]],
        )
        created.append(ch.slug)
    return JSONResponse({"slug": slug, "suggestions": sugg, "created": created})


@router.get("/stats")
async def get_stats(
    request: Request,
    board: str | None = Query(default=None),
    _auth: object = Depends(require_device_or_loopback),
) -> JSONResponse:
    """Aggregate board metrics for the Stats tab. Tokens are reported
    SEPARATELY for hive vs claude — never summed into one number.

    P2 v-Next: when ``?board=<board_id>`` is provided, stats are computed
    only over that board's tasks. When omitted (back-compat), all tasks.
    """
    # Only use the stats cache when NOT scoping to a specific board, to avoid
    # serving wrong-board data from a cached unscoped result.
    if board is None:
        cached = getattr(request.app.state, "crew_stats_cache", None)
        now = time.monotonic()
        if cached is not None and (now - cached[0]) < _STATS_TTL_S:
            return JSONResponse(cached[1])
    store = _store(request)
    tasks = store.list_tasks(board_id=board)
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
        # P7: bench/loop/goal surfacing.
        "bench_scores": _bench_model_scores(),
        "loop_decisions": _loop_decisions(),
        "goal_cycles": _goal_cycle_stats(store),
    }
    if board is None:
        request.app.state.crew_stats_cache = (time.monotonic(), payload)
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
async def get_task_diff(
    request: Request, slug: str,
    _auth: object = Depends(require_device_or_loopback),
) -> JSONResponse:
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
async def get_lessons(
    request: Request, limit: int = 50,
    _auth: object = Depends(require_device_or_loopback),
) -> JSONResponse:
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


def _bench_model_scores() -> list[dict]:
    """Load per-role model scores from state/bench_results.json.
    Returns [{role, model, quality, latency_ms, cost_per_1k, composite}, ...]."""
    try:
        from gateway.orchestrator.bench_results import load_results, BenchScore
        results = load_results(_BENCH_RESULTS_PATH)
        out: list[dict] = []
        # Inline composite calc (mirrors Router weights but avoids importing
        # the full catalog — stats is read-only, doesn't need routing logic).
        Q_W, L_W, C_W = 0.5, 0.3, 0.2
        L_ANCHOR, C_ANCHOR = 500.0, 0.001
        for role, per_role in results.scores.items():
            for model_id, s in per_role.items():
                lat_norm = min(L_ANCHOR / max(s.latency_p50_ms, 1.0), 1.0)
                cost_norm = 1.0 if s.cost_per_1k_tokens <= 0 else min(C_ANCHOR / s.cost_per_1k_tokens, 1.0)
                composite = Q_W * s.quality_score + L_W * lat_norm + C_W * cost_norm
                out.append({
                    "role": role,
                    "model": model_id,
                    "quality": round(s.quality_score, 3),
                    "latency_ms": round(s.latency_p50_ms, 1),
                    "cost_per_1k": round(s.cost_per_1k_tokens, 5),
                    "composite": round(composite, 3),
                })
        return out
    except Exception:
        return []


def _loop_decisions() -> list[dict]:
    """Load loop_decisions.json (Thread B adopt/reject per role/model).
    Returns [{role, model, adopt, delta, single_q, loop_q}, ...]."""
    try:
        if not _LOOP_DECISIONS_PATH.is_file():
            return []
        raw = json.loads(_LOOP_DECISIONS_PATH.read_text(encoding="utf-8"))
        out: list[dict] = []
        for role, models in raw.items():
            if not isinstance(models, dict):
                continue
            for model_id, d in models.items():
                if not isinstance(d, dict):
                    continue
                out.append({
                    "role": role,
                    "model": model_id,
                    "adopt": bool(d.get("adopt")),
                    "delta": round(float(d.get("delta", 0)), 3),
                    "single_q": round(float(d.get("single_mean", 0)), 3),
                    "loop_q": round(float(d.get("loop_mean", 0)), 3),
                })
        return out
    except Exception:
        return []


def _goal_cycle_stats(store: CrewBoardStore) -> dict:
    """Summarize goal-loop state from crew_meta goal:* records.
    Returns {active, complete, needs_you, total, max_cycle, goals: [...]}."""
    try:
        from gateway.crew_board.goal_loop import GoalRecord
        rows = store.list_meta_like("goal:%")
        active = complete = needs_you = 0
        max_cycle = 0
        goals: list[dict] = []
        for _key, value in rows:
            try:
                gr = GoalRecord.from_json(value)
            except Exception:
                continue
            if gr.status == "active":
                active += 1
            elif gr.status == "complete":
                complete += 1
            elif gr.status == "needs_you":
                needs_you += 1
            max_cycle = max(max_cycle, gr.cycle)
            goals.append({
                "goal_id": gr.goal_id,
                "text": gr.text[:80],
                "project": gr.project_slug,
                "status": gr.status,
                "cycle": gr.cycle,
                "checklist_met": sum(1 for c in gr.checklist if c.get("met")),
                "checklist_total": len(gr.checklist),
            })
        return {
            "active": active,
            "complete": complete,
            "needs_you": needs_you,
            "total": len(goals),
            "max_cycle": max_cycle,
            "goals": goals[:20],  # cap for payload size
        }
    except Exception:
        return {"active": 0, "complete": 0, "needs_you": 0, "total": 0, "goals": []}


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
        # P6: goal-level acceptance checklist. Each item is a concrete,
        # measurable statement of what "done" means for the WHOLE GOAL —
        # distinct from per-ticket acceptance_criteria. The verify runner
        # checks these items against the codebase after all subtasks finish.
        # Example: "file src/auth.py exists and exports AuthService",
        #          "GET /api/v1/auth returns 200 for valid credentials".
        "checklist": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-5 goal-level items: the goal is met when ALL of these are true.",
        },
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

7. CHECKLIST (required, 2-5 items)  — a top-level "checklist" array that
   expresses what "done" means for the WHOLE GOAL (not per-ticket). Each
   item is a single, concrete, machine-testable statement:
     • "file src/auth.py exists and exports class AuthService"
     • "GET /api/v1/auth returns 200 for valid credentials"
     • "pytest gateway/tests/test_auth.py passes with exit 0"
   These are checked by a verify runner AFTER all tickets finish. If they
   are not all met, new subtasks are auto-created (up to 3 cycles). Write
   2-5 items that collectively prove the goal is shipped.

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


def _infer_test_cmd(project_path: str) -> str:
    """Best-effort test command from a project's marker files, for an existing
    project that has no stored test_cmd. Mirrors `_stack_hint`'s detection."""
    from pathlib import Path as _P
    p = _P(project_path)
    if (p / "pubspec.yaml").is_file():
        return "flutter test"
    if (p / "Cargo.toml").is_file():
        return "cargo test"
    if (p / "go.mod").is_file():
        return "go test ./..."
    if (p / "pyproject.toml").is_file() or (p / "setup.py").is_file():
        return "pytest"
    if (p / "package.json").is_file():
        return "npm test"
    return ""  # unknown — verifier falls back to its file-glob / smoke gates


_AUTO_MATCH_SYSTEM = (
    "You route a one-line software goal to the project it belongs to. You are "
    "given a catalog of existing projects — each with a slug, a name, and a few "
    "recent task titles that reveal what the project actually IS (judge by the "
    "TASK TITLES, not the name: a slug like 'example-app' whose tasks mention "
    "castling/check is a chess app). Pick the existing project the goal "
    "CONTINUES (same app, codebase, or domain), or 'NEW' only if the goal "
    "genuinely fits none of them. Respond with JSON only."
)


def _auto_project_catalog(store, projects) -> str:
    """Render the project catalog with recent task titles for topic signal.

    A bare 'slug: name' line is too weak for the classifier (e.g. 'example-app'
    does not read as chess). Recent task titles tell the model what each project
    is about, so the goal can be routed to the right existing project."""
    titles_by_proj: dict[str, list[str]] = {}
    try:
        for t in store.list_tasks():
            if t.title:
                titles_by_proj.setdefault(t.project_slug, []).append(t.title)
    except Exception:  # noqa: BLE001
        titles_by_proj = {}
    lines = []
    for p in projects:
        recent = titles_by_proj.get(p.slug, [])[:6]
        topic = (" — recent tasks: " + "; ".join(recent)) if recent else ""
        lines.append(f"- {p.slug} ({p.name}){topic}")
    return "\n".join(lines)


async def _auto_resolve_project(store, goal: str) -> str:
    """AUTO mode: classify *goal* against the enabled projects.

    Returns an existing project's slug when the goal continues it, or "" to
    signal a brand-new (greenfield) project. Returns "" on any parse/transport
    failure — a stray new project is recoverable; a wrong match is not.
    """
    import logging
    from gateway.helpers.base import OllamaInvoker, extract_json
    log = logging.getLogger("gateway.board")

    projects = store.list_projects(enabled_only=True)
    if not projects:
        return ""   # nothing to match → greenfield
    slugs = [p.slug for p in projects]
    catalog = _auto_project_catalog(store, projects)
    schema = {
        "type": "object",
        "properties": {
            "match": {"type": "string", "enum": [*slugs, "NEW"]},
            "reason": {"type": "string"},
        },
        "required": ["match"],
    }
    try:
        text, _, _ = await OllamaInvoker().chat(
            model="planner-qwen", system=_AUTO_MATCH_SYSTEM,
            user=(f"Existing projects:\n{catalog}\n\nGoal: {goal}\n\n"
                  'Reply JSON: {"match": "<slug>"|"NEW", "reason": "..."}'),
            params={"temperature": 0.1, "num_ctx": 8192, "num_predict": 256},
            fmt=schema,
        )
        d = extract_json(text)
        match = str((d or {}).get("match", "NEW")).strip()
        if match in set(slugs):
            log.info("decompose auto: goal routed to existing project %r", match)
            return match
        log.info("decompose auto: goal classified as a NEW project")
        return ""
    except Exception as e:  # noqa: BLE001
        log.warning("decompose auto: classify failed (%s) → new project", e)
        return ""


def _capture_preflight_baseline(store, project_slug: str) -> None:
    """Run the project's suite ONCE at chain start and record the set of
    already-failing tests in crew_meta (`preflight:failing:<slug>`). The
    verifier's baseline-diff then passes a ticket as long as it adds NO NEW
    failures — so a pre-existing broken or flaky test cannot freeze a whole
    chain whose own work is correct. Best-effort; never raises."""
    import json as _json
    import logging as _logging
    _log = _logging.getLogger("gateway.board")
    key = f"preflight:failing:{project_slug}"
    try:
        from gateway.crew_board.verifier import _run_tests
        proj = store.get_project(project_slug)
        if proj is None or not getattr(proj, "test_cmd", None):
            store.set_meta(key, "[]")
            return
        res = _run_tests(proj)
        if not res.get("ran"):
            store.set_meta(key, "[]")  # couldn't run → no baseline; verifier stays strict
            return
        failing = res.get("failed_ids") or []
        store.set_meta(key, _json.dumps(failing))
        store.set_meta(f"preflight:ok:{project_slug}",
                       "1" if res.get("exit_code") == 0 else "0")
        _log.info("preflight baseline for %s: %d pre-existing failing test(s)",
                  project_slug, len(failing))
    except Exception as e:  # noqa: BLE001
        _log.warning("preflight baseline capture failed for %s: %s", project_slug, e)
        try:
            store.set_meta(key, "[]")
        except Exception:  # noqa: BLE001
            pass


@router.post("/decompose")
async def decompose_goal(
    request: Request,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """NL goal → an LLM-generated, dependency-chained ticket plan, created
    on the board ready for the hive. Scaffolds a new project (local dir +
    git + enabled + push_allowed) when no project_slug is given.

    project_slug accepts: an existing slug (target it), "" (always create a
    new project), or "auto" (classify the goal → an existing project when it
    clearly continues one, else create a new project)."""
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

    # AUTO mode: classify the goal → an existing project slug (continue it) or
    # "" (greenfield). Done BEFORE the existing/greenfield branch below so the
    # stack detection + scaffolding pick the right path.
    if project_slug.lower() == "auto":
        project_slug = await _auto_resolve_project(store, goal)

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
    # coder lane on qwen2.5-coder:7b).
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
    if existing_proj is not None:
        # Existing project: use its stored test_cmd; if it has none (test_cmd is
        # None), infer one from its marker files — NEVER index gf here (gf is None
        # for existing projects, so `gf["test_cmd"]` was a raw-500 TypeError that
        # surfaced in the UI as "Unexpected token 'I', Internal S... not valid JSON").
        test_cmd = getattr(existing_proj, "test_cmd", None) or _infer_test_cmd(existing_proj.path)
    else:
        test_cmd = gf["test_cmd"]
    if not project_slug:
        name = str(plan.get("project_name") or "new-project")
        slug = _re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "new-project"
        # Dir name == slug (kebab), forward-slashed, so the project scanner
        # re-derives the SAME slug from the directory and never mints a
        # squashed-name duplicate (the old 'androidtetrisgame' twin bug).
        path = _PROJECTS_ROOT / slug
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
            # Fail LOUD if the scaffold did not actually produce a runnable tree
            # (e.g. flutter/cargo/go not on PATH) — otherwise the project
            # registers empty and EVERY ticket wedges on "project path missing"/no
            # tests (the android-tetris-game case). gradle/godot/None scaffold from
            # inside the hive, so they're exempt (no marker required).
            _sk = (gf or {}).get("scaffold_kind")
            _marker = {"flutter": "pubspec.yaml", "node": "package.json",
                       "rust": "Cargo.toml", "go": "go.mod",
                       "python": "tests"}.get(_sk or "")
            if _marker and not (path / _marker).exists():
                raise HTTPException(500, (
                    f"greenfield scaffold ({_sk}) did not produce '{_marker}' in "
                    f"{path} — is the toolchain installed and on PATH? Refusing to "
                    f"create a non-runnable project that would wedge every ticket."))
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

    # Pre-flight: capture the project's ALREADY-failing tests as a baseline so the
    # verifier's baseline-diff lets each ticket pass as long as it adds NO NEW
    # failures (a pre-existing broken/flaky test must not freeze the chain). Run
    # off-thread so the full suite doesn't block the event loop.
    import asyncio as _asyncio
    await _asyncio.to_thread(_capture_preflight_baseline, store, project_slug)

    # P6: Create the goal record from the planner's top-level checklist.
    # The goal_id groups all subtasks so the completion trigger can detect
    # when they are all done and auto-spawn the verify ticket.
    from gateway.crew_board.goal_loop import create_goal, goal_tag as _goal_tag
    checklist_items = [
        str(item) for item in (plan.get("checklist") or [])
        if str(item).strip()
    ][:8]
    if not checklist_items:
        # Planner didn't emit a checklist (older model / fallback) — derive
        # one from the goal text so the loop still functions.
        checklist_items = [f"Goal achieved: {goal[:120]}"]
    p6_goal = create_goal(
        store,
        text=goal,
        project_slug=project_slug,
        checklist_items=checklist_items,
        cycle=0,
    )
    goal_id_for_tasks = p6_goal.goal_id

    # Create the chained tickets. The whole tail (DB writes + FSM moves) is
    # wrapped so ANY failure returns a JSON error envelope — never a raw 500
    # "Internal Server Error", which the dashboard would try to JSON.parse and
    # surface as a confusing "Unexpected token 'I'" message.
    try:
        # First pass: create all tasks (no depends_on yet — we need the slug
        # list to translate 0-based LLM indexes into real task slugs).
        slugs, titles, raw_tickets = [], [], []
        for t in plan["tickets"][:12]:
            title = str(t.get("title", "")).strip()[:120]
            if not title:
                continue
            crit = [{"text": str(c), "checked": False}
                    for c in (t.get("criteria") or [])][:6]
            # P6: stamp every subtask with the goal_id (column + tag).
            task = store.create_task(
                title=title, project_slug=project_slug,
                body=str(t.get("body", "")), created_by="owner",
                acceptance_criteria=crit,
                files_of_interest=[str(f) for f in (t.get("files") or [])][:8],
                tags=["nl-decompose", _goal_tag(goal_id_for_tasks)],
                review_by="claude-code",
                goal_id=goal_id_for_tasks,
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
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"board create failed after planning: {e}")
    return JSONResponse({
        "project_slug": project_slug, "scaffolded": scaffolded,
        "created": len(slugs), "titles": titles,
        "goal_id": goal_id_for_tasks,
        "checklist": checklist_items,
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
    if not _SLUG_RE.fullmatch(project_slug):
        raise HTTPException(
            400, "invalid project_slug (lowercase letters, digits, '.', '_', '-' only)"
        )
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
            board_id=str(payload.get("board_id", "default")),
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


@router.post("/tasks/{slug}/depends")
async def set_task_depends(
    request: Request,
    slug: str,
    payload: dict = Body(...),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """#172: manually set a task's blockers. Body: {"depends_on": ["T-1234", ...]}.
    The dispatcher won't claim this task until every listed task is done.
    Rejects self-reference, unknown slugs, and direct cycles."""
    store = _store(request)
    deps = payload.get("depends_on", [])
    if not isinstance(deps, list):
        raise HTTPException(400, "depends_on must be a list of task slugs")
    try:
        t = store.set_depends_on(
            slug, [str(d) for d in deps],
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
    # Path is either explicit or derived from _PROJECTS_ROOT/<name>.
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


@router.post("/projects/{slug}/delete")
async def delete_project_route(
    request: Request,
    slug: str,
    payload: dict = Body(default={}),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Remove a project from the board. SAFETY: refuses if the project still
    owns tasks unless force=true — prevents orphaning live work. Used to prune
    duplicate / dead project rows (e.g. the squashed-name scanner twins)."""
    store = _store(request)
    if store.get_project(slug) is None:
        raise HTTPException(404, f"unknown project {slug!r}")
    n_tasks = sum(1 for t in store.list_tasks() if t.project_slug == slug)
    if n_tasks > 0 and not bool(payload.get("force")):
        raise HTTPException(
            409, f"project {slug!r} has {n_tasks} task(s); pass force=true to delete anyway")
    deleted = store.delete_project(slug)
    notifier = getattr(request.app.state, "crew_notifier", None)
    if notifier is not None:
        notifier.broadcast({"event": "project_deleted", "project": slug})
    return JSONResponse({"deleted": deleted, "slug": slug, "had_tasks": n_tasks})


# ─── Evolve lane: one-click continuous development (EV2) ──────────────────────
#
# Suggest = analyze the done project → ranked "what's next" candidates (persisted
# so Go builds the SAME top idea the owner saw). Go = take the top candidate and
# feed it through the existing decompose pipeline (planner → goal → chained
# tickets the hive builds). One goal per click; never pushes (decompose leaves an
# existing project's push_allowed untouched).

_EVOLVE_META = "evolve:{slug}"


def _evolve_active_task_count(store, slug: str) -> int:
    """How many non-terminal tasks the project still has (a project is 'done'
    when this is 0)."""
    from gateway.crew_board import schema as _schema
    live = {_schema.STATUS_PROPOSED, _schema.STATUS_BACKLOG, _schema.STATUS_READY,
            _schema.STATUS_IN_PROGRESS, _schema.STATUS_QA, _schema.STATUS_REVIEW}
    return sum(1 for t in store.list_tasks()
               if t.project_slug == slug and t.status in live)


@router.post("/projects/{slug}/evolve/suggest")
async def evolve_suggest(
    request: Request,
    slug: str,
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Analyze a project → ranked next-work candidates. Persists them so a later
    Go builds the same top idea."""
    import json as _json
    from gateway.crew_board.evolve import analyze_next
    store = _store(request)
    if store.get_project(slug) is None:
        raise HTTPException(404, f"unknown project {slug!r}")
    cands = await analyze_next(store, slug)
    data = [c.to_dict() for c in cands]
    try:
        store.set_meta(_EVOLVE_META.format(slug=slug), _json.dumps({"candidates": data}))
    except Exception as e:  # noqa: BLE001
        log.warning("evolve suggest: persist failed for %s: %s", slug, e)
    return JSONResponse({"slug": slug, "candidates": data})


@router.post("/projects/{slug}/evolve/go")
async def evolve_go(
    request: Request,
    slug: str,
    payload: dict = Body(default={}),
    _auth: None = Depends(_require_board_auth),
) -> JSONResponse:
    """Build the top 'what's next' candidate: decompose it into a goal + chained
    tickets the hive picks up. Uses the cached Suggest result when present, else
    analyzes fresh. ONE goal per click; never pushes."""
    import json as _json
    import logging as _logging
    from gateway.crew_board.evolve import analyze_next
    log2 = _logging.getLogger("gateway.board")
    store = _store(request)
    if store.get_project(slug) is None:
        raise HTTPException(404, f"unknown project {slug!r}")

    # Guardrail: only "evolve" a done project unless explicitly forced.
    active = _evolve_active_task_count(store, slug)
    if active > 0 and not bool(payload.get("force")):
        raise HTTPException(
            409, f"project {slug!r} still has {active} active task(s); finish or pass force=true")

    # Prefer the candidate the owner saw in Suggest; else analyze fresh.
    top: dict | None = None
    raw = store.get_meta(_EVOLVE_META.format(slug=slug))
    if raw:
        try:
            cands = (_json.loads(raw) or {}).get("candidates") or []
            if cands:
                top = cands[0]
        except (ValueError, TypeError):
            top = None
    if top is None:
        fresh = await analyze_next(store, slug)
        if fresh:
            top = fresh[0].to_dict()
    if top is None:
        raise HTTPException(422, f"no next-work candidate found for {slug!r}")

    goal_text = f"{top.get('title', '').strip()}\n\n{top.get('body', '').strip()}".strip()
    # Reuse the proven decompose pipeline in-process: planner → goal record →
    # dependency-chained tickets, on the EXISTING project (no scaffold, no push).
    resp = await decompose_goal(request, {"goal": goal_text, "project_slug": slug}, None)
    try:
        out = _json.loads(bytes(resp.body).decode("utf-8"))
    except Exception:  # noqa: BLE001
        out = {}
    out["evolved_from"] = top.get("title", "")
    log2.info("evolve go: %s → goal %r (%d tickets)", slug, top.get("title"), out.get("created", 0))
    return JSONResponse(out, status_code=resp.status_code)


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
    # SECURITY (audit C2): only embed the mutation token for LOOPBACK callers.
    # The gateway binds the Tailscale IP, so serving _BOARD_TOKEN in the HTML to
    # everyone let any tailnet device scrape it and perform board mutations,
    # defeating the loopback gate on /board/session-token. Loopback (the wallpaper
    # dashboard) still gets the token; tailnet/LAN get an empty token and the page
    # JS falls back to /board/session-token (also loopback-only) — so remote
    # clients must present a real device Bearer on each mutation (path (b)).
    from gateway.deps import _is_loopback
    client = request.client
    token = _BOARD_TOKEN if (client is not None and _is_loopback(client.host)) else ""
    html = (
        _BOARD_HTML
        .replace("{{BOARD_TOKEN}}", token)
        .replace("{{BODY_CLASS}}", body_class)
    )
    csp = _BOARD_CSP_EMBED if embed else _BOARD_CSP
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


_BOARD_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="board-token" content="{{BOARD_TOKEN}}"/>
<script>
/* Theme sync — same-origin iframe shares localStorage with the dashboard.
   Runs synchronously before first paint to avoid flash.
   Key: 'hive.theme', values: holo/terminal/brutalist/vector-tron/glitch-mag/hive-v2 */
(function () {
  var THEMES = ['holo','terminal','brutalist','vector-tron','glitch-mag','hive-v2','joker','nod','synthwave','daybreak','royal','weatherstar','retro-purple','inverted','zombie','code-fall','winter','code-red'];
  function applyTheme(t) {
    if (!t || THEMES.indexOf(t) < 0) t = 'hive-v2';
    document.documentElement.dataset.theme = t;
  }
  var cur;
  try { cur = localStorage.getItem('hive.theme'); } catch(e) { cur = null; }
  applyTheme(cur || 'hive-v2');
  /* Live recolor when the dashboard picker changes theme (storage event from
     another tab/frame) or the parent posts a message (belt+suspenders for the
     embedded iframe). */
  window.addEventListener('storage', function(e) {
    if (e.key === 'hive.theme') applyTheme(e.newValue);
  });
  window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'theme' && e.data.name) applyTheme(e.data.name);
  });
  /* Standalone /board (opened directly in a browser, not embedded): there's no
     parent to feed the theme, so pull the server-held theme and poll it. Same
     origin as the gateway, so the GET is free. Keeps a directly-opened board in
     sync with whatever the dashboard last set. */
  function pullServerTheme() {
    fetch('/v1/theme', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && j.theme) applyTheme(j.theme); })
      .catch(function () {});
  }
  pullServerTheme();
  setInterval(pullServerTheme, 5000);
})();
</script>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cg fill='%23E0A445'%3E%3Cpath d='M7 3h4l2 3.5L11 10H7L5 6.5z'/%3E%3Cpath d='M14 3h4l2 3.5L18 10h-4l-2-3.5z' opacity='.55'/%3E%3Cpath d='M7 13h4l2 3.5L11 20H7l-2-3.5z' opacity='.55'/%3E%3Cpath d='M14 13h4l2 3.5L18 20h-4l-2-3.5z'/%3E%3C/g%3E%3C/svg%3E"/>
<title>Crew Board</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet"/>
<style>
  /* Hive ecosystem theme — warm near-black base, copper/amber accents,
     green=live, cyan=telemetry/claude, red=error. OKLCH per DESIGN.md;
     never pure #000/#fff. Aligned with the Flutter app + dashboard.
     Default = hive-v2 (warm-black/amber). Override via data-theme attr. */
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
    /* Derived surface tokens — used in structural CSS, override in theme blocks. */
    --scrollbar-thumb: oklch(0.30 0.02 64);
    --body-grad-top:   oklch(0.17 0.03 60);
    --body-grad-bot:   oklch(0.10 0.01 55);
    --dialog-backdrop: oklch(0.10 0.01 55 / 0.60);
    --pre-bg:          oklch(0.12 0.012 58);
    /* canvas chart colors (hex for Canvas 2D API) */
    --chart-grid:      #3a342c;
    --chart-green:     #5cc870;
    --chart-cyan:      #60c8c8;
    --chart-label:     #8a8780;
  }

  /* ── hive-v2 — warm-black command center, amber honeycomb brand (default). */
  html[data-theme="hive-v2"] {
    --bg:       oklch(0.14 0.014 55);
    --panel:    oklch(0.17 0.016 58);
    --card:     oklch(0.20 0.018 60);
    --card-hi:  oklch(0.24 0.020 60);
    --line:     oklch(0.30 0.02  64);
    --txt:      oklch(0.95 0.012 72);
    --txt-dim:  oklch(0.74 0.014 72);
    --faint:    oklch(0.56 0.014 68);
    --copper:   oklch(0.74 0.13  56);
    --accent:   oklch(0.83 0.15  78);
    --amber-glow:oklch(0.85 0.17 80);
    --green:    oklch(0.80 0.16 150);
    --cyan:     oklch(0.80 0.10 200);
    --red:      oklch(0.66 0.17  25);
    --on-amber: #1A0E00;
    --scrollbar-thumb: oklch(0.30 0.02 64);
    --body-grad-top:   oklch(0.17 0.03 60);
    --body-grad-bot:   oklch(0.10 0.01 55);
    --dialog-backdrop: oklch(0.10 0.01 55 / 0.60);
    --pre-bg:          oklch(0.12 0.012 58);
    --chart-grid:      #3a342c;
    --chart-green:     #5cc870;
    --chart-cyan:      #60c8c8;
    --chart-label:     #8a8780;
  }

  /* ── holo — iridescent deep violet-black, cyan accent, magenta highlights. */
  html[data-theme="holo"] {
    --bg:       oklch(0.10 0.025 290);
    --panel:    oklch(0.15 0.03  290);
    --card:     oklch(0.22 0.035 290);
    --card-hi:  oklch(0.27 0.04  290);
    --line:     oklch(0.33 0.04  290);
    --txt:      oklch(0.96 0.008 240);
    --txt-dim:  oklch(0.72 0.02  240);
    --faint:    oklch(0.55 0.02  240);
    --copper:   oklch(0.68 0.27  340);
    --accent:   oklch(0.82 0.18   70);
    --amber-glow:oklch(0.86 0.20  72);
    --green:    oklch(0.88 0.22  130);
    --cyan:     oklch(0.82 0.16  200);
    --red:      oklch(0.66 0.17   25);
    --on-amber: #130800;
    --scrollbar-thumb: oklch(0.28 0.04 290);
    --body-grad-top:   oklch(0.16 0.04 290);
    --body-grad-bot:   oklch(0.08 0.02 290);
    --dialog-backdrop: oklch(0.06 0.02 290 / 0.60);
    --pre-bg:          oklch(0.17 0.03 290);
    --chart-grid:      #302845;
    --chart-green:     #9adc40;
    --chart-cyan:      #60ccc8;
    --chart-label:     #8a87aa;
  }

  /* ── terminal — green-on-black CRT phosphor. Lime text, near-black ground. */
  html[data-theme="terminal"] {
    --bg:       oklch(0.08 0.04  150);
    --panel:    oklch(0.12 0.05  145);
    --card:     oklch(0.14 0.05  145);
    --card-hi:  oklch(0.18 0.06  142);
    --line:     oklch(0.25 0.07  140);
    --txt:      oklch(0.92 0.18  130);
    --txt-dim:  oklch(0.78 0.16  130);
    --faint:    oklch(0.55 0.12  130);
    --copper:   oklch(0.78 0.20   95);
    --accent:   oklch(0.88 0.22  130);
    --amber-glow:oklch(0.90 0.22 132);
    --green:    oklch(0.88 0.22  130);
    --cyan:     oklch(0.80 0.18  160);
    --red:      oklch(0.65 0.18   25);
    --on-amber: #030d06;
    --scrollbar-thumb: oklch(0.22 0.06 140);
    --body-grad-top:   oklch(0.13 0.05 148);
    --body-grad-bot:   oklch(0.06 0.03 150);
    --dialog-backdrop: oklch(0.04 0.03 150 / 0.65);
    --pre-bg:          oklch(0.10 0.04 148);
    --chart-grid:      #153020;
    --chart-green:     #9aee60;
    --chart-cyan:      #50c878;
    --chart-label:     #4a9030;
  }

  /* ── brutalist — stark near-monochrome, no glow. */
  html[data-theme="brutalist"] {
    --bg:       oklch(0.06 0 0);
    --panel:    oklch(0.12 0 0);
    --card:     oklch(0.14 0 0);
    --card-hi:  oklch(0.20 0 0);
    --line:     oklch(0.28 0 0);
    --txt:      oklch(0.98 0 0);
    --txt-dim:  oklch(0.75 0 0);
    --faint:    oklch(0.55 0 0);
    --copper:   oklch(0.92 0.005 240);
    --accent:   oklch(0.98 0 0);
    --amber-glow:oklch(1 0 0);
    --green:    oklch(0.85 0.005 240);
    --cyan:     oklch(0.92 0.005 240);
    --red:      oklch(0.70 0 0);
    --on-amber: #0f0f0f;
    --scrollbar-thumb: oklch(0.28 0 0);
    --body-grad-top:   oklch(0.10 0 0);
    --body-grad-bot:   oklch(0.04 0 0);
    --dialog-backdrop: oklch(0.04 0 0 / 0.70);
    --pre-bg:          oklch(0.10 0 0);
    --chart-grid:      #404040;
    --chart-green:     #d0d0d8;
    --chart-cyan:      #e8e8f0;
    --chart-label:     #808080;
  }

  /* ── vector-tron — neon electric blue/violet on near-black. */
  html[data-theme="vector-tron"] {
    --bg:       oklch(0.06 0.05  280);
    --panel:    oklch(0.12 0.06  278);
    --card:     oklch(0.14 0.06  278);
    --card-hi:  oklch(0.19 0.07  276);
    --line:     oklch(0.26 0.08  270);
    --txt:      oklch(0.95 0.04  250);
    --txt-dim:  oklch(0.70 0.10  250);
    --faint:    oklch(0.50 0.08  250);
    --copper:   oklch(0.65 0.30  320);
    --accent:   oklch(0.85 0.20  220);
    --amber-glow:oklch(0.88 0.22 222);
    --green:    oklch(0.78 0.18  160);
    --cyan:     oklch(0.85 0.20  220);
    --red:      oklch(0.66 0.17   25);
    --on-amber: #050612;
    --scrollbar-thumb: oklch(0.22 0.07 278);
    --body-grad-top:   oklch(0.12 0.07 278);
    --body-grad-bot:   oklch(0.04 0.04 280);
    --dialog-backdrop: oklch(0.04 0.04 280 / 0.65);
    --pre-bg:          oklch(0.10 0.055 278);
    --chart-grid:      #1e2450;
    --chart-green:     #30d8a0;
    --chart-cyan:      #3090f8;
    --chart-label:     #5060a0;
  }

  /* ── glitch-mag — editorial warm ink, cyan/magenta glitch accents. */
  html[data-theme="glitch-mag"] {
    --bg:       oklch(0.08 0.015  60);
    --panel:    oklch(0.13 0.018  60);
    --card:     oklch(0.15 0.018  60);
    --card-hi:  oklch(0.20 0.020  60);
    --line:     oklch(0.27 0.02   60);
    --txt:      oklch(0.97 0.005  60);
    --txt-dim:  oklch(0.65 0.02  240);
    --faint:    oklch(0.45 0.02  240);
    --copper:   oklch(0.68 0.27  340);
    --accent:   oklch(0.82 0.16  200);
    --amber-glow:oklch(0.86 0.18 202);
    --green:    oklch(0.72 0.18  150);
    --cyan:     oklch(0.82 0.16  200);
    --red:      oklch(0.66 0.17   25);
    --on-amber: #050b0b;
    --scrollbar-thumb: oklch(0.24 0.02 60);
    --body-grad-top:   oklch(0.13 0.02 60);
    --body-grad-bot:   oklch(0.06 0.01 60);
    --dialog-backdrop: oklch(0.06 0.01 60 / 0.65);
    --pre-bg:          oklch(0.12 0.016 58);
    --chart-grid:      #342e24;
    --chart-green:     #40c870;
    --chart-cyan:      #40c8d0;
    --chart-label:     #606878;
  }

  /* ── joker — deep purple ground, acid-green accent. */
  html[data-theme="joker"] {
    --bg:       oklch(0.12 0.05  300);
    --panel:    oklch(0.17 0.06  300);
    --card:     oklch(0.20 0.07  300);
    --card-hi:  oklch(0.25 0.08  300);
    --line:     oklch(0.32 0.08  300);
    --txt:      oklch(0.96 0.008 300);
    --txt-dim:  oklch(0.74 0.02  300);
    --faint:    oklch(0.56 0.02  300);
    --copper:   oklch(0.70 0.22  300);
    --accent:   oklch(0.82 0.22  142);
    --amber-glow:oklch(0.84 0.24 142);
    --green:    oklch(0.82 0.22  142);
    --cyan:     oklch(0.78 0.16  160);
    --red:      oklch(0.66 0.17   25);
    --on-amber: #060e05;
    --scrollbar-thumb: oklch(0.28 0.07 300);
    --body-grad-top:   oklch(0.18 0.07 300);
    --body-grad-bot:   oklch(0.09 0.04 300);
    --dialog-backdrop: oklch(0.09 0.04 300 / 0.65);
    --pre-bg:          oklch(0.15 0.055 300);
    --chart-grid:      #2a1d3a;
    --chart-green:     #80e840;
    --chart-cyan:      #40d890;
    --chart-label:     #706080;
  }

  /* ── nod — near-black warm ground, crimson red accent, dim-green ok status. */
  html[data-theme="nod"] {
    --bg:       oklch(0.08 0.012  25);
    --panel:    oklch(0.13 0.025  25);
    --card:     oklch(0.16 0.028  25);
    --card-hi:  oklch(0.21 0.032  25);
    --line:     oklch(0.28 0.035  25);
    --txt:      oklch(0.95 0.006  25);
    --txt-dim:  oklch(0.72 0.018  25);
    --faint:    oklch(0.52 0.018  25);
    --copper:   oklch(0.58 0.23   25);
    --accent:   oklch(0.58 0.23   25);
    --amber-glow:oklch(0.62 0.25  25);
    --green:    oklch(0.64 0.14  145);
    --cyan:     oklch(0.70 0.10  190);
    --red:      oklch(0.58 0.23   25);
    --on-amber: #1a0404;
    --scrollbar-thumb: oklch(0.24 0.03 25);
    --body-grad-top:   oklch(0.13 0.028 25);
    --body-grad-bot:   oklch(0.06 0.010 25);
    --dialog-backdrop: oklch(0.06 0.010 25 / 0.65);
    --pre-bg:          oklch(0.11 0.020 25);
    --chart-grid:      #2c1818;
    --chart-green:     #50a860;
    --chart-cyan:      #508090;
    --chart-label:     #705050;
  }

  /* ── synthwave — 80s retro sunset. Deep indigo, hot-magenta accent, cyan secondary. */
  html[data-theme="synthwave"] {
    --bg:       oklch(0.10 0.04  295);
    --panel:    oklch(0.15 0.05  295);
    --card:     oklch(0.18 0.055 295);
    --card-hi:  oklch(0.24 0.07  295);
    --line:     oklch(0.32 0.08  295);
    --txt:      oklch(0.94 0.020 300);
    --txt-dim:  oklch(0.72 0.04  295);
    --faint:    oklch(0.54 0.06  295);
    --copper:   oklch(0.68 0.30  335);
    --accent:   oklch(0.68 0.30  335);
    --amber-glow:oklch(0.72 0.28 335);
    --green:    oklch(0.84 0.18  145);
    --cyan:     oklch(0.80 0.20  205);
    --red:      oklch(0.66 0.17   25);
    --on-amber: #08000e;
    --scrollbar-thumb: oklch(0.28 0.07 295);
    --body-grad-top:   oklch(0.16 0.06 295);
    --body-grad-bot:   oklch(0.07 0.03 295);
    --dialog-backdrop: oklch(0.07 0.03 295 / 0.65);
    --pre-bg:          oklch(0.14 0.05 295);
    --chart-grid:      #1c1030;
    --chart-green:     #70e860;
    --chart-cyan:      #20d8f8;
    --chart-label:     #7050a0;
  }

  /* ── daybreak — warm paper light theme. Light bg, dark ink, deep teal accent. */
  html[data-theme="daybreak"] {
    --bg:       oklch(0.97 0.008  80);
    --panel:    oklch(0.92 0.010  78);
    --card:     oklch(0.88 0.012  76);
    --card-hi:  oklch(0.84 0.014  74);
    --line:     oklch(0.76 0.016  72);
    --txt:      oklch(0.18 0.030 220);
    --txt-dim:  oklch(0.36 0.025 220);
    --faint:    oklch(0.52 0.020 220);
    --copper:   oklch(0.46 0.12  192);
    --accent:   oklch(0.46 0.12  192);
    --amber-glow:oklch(0.50 0.14 192);
    --green:    oklch(0.38 0.10  150);
    --cyan:     oklch(0.46 0.12  192);
    --red:      oklch(0.50 0.20   25);
    --on-amber: #f0ece0;
    --scrollbar-thumb: oklch(0.74 0.016 76);
    --body-grad-top:   oklch(0.94 0.010 80);
    --body-grad-bot:   oklch(0.90 0.012 78);
    --dialog-backdrop: oklch(0.70 0.010 78 / 0.55);
    --pre-bg:          oklch(0.91 0.010 78);
    --chart-grid:      #c8c2b4;
    --chart-green:     #2a7030;
    --chart-cyan:      #1a6868;
    --chart-label:     #606878;
  }

  /* ── royal — deep navy ground, warm gold accent, ivory text. */
  html[data-theme="royal"] {
    --bg:       oklch(0.12 0.04  250);
    --panel:    oklch(0.17 0.05  250);
    --card:     oklch(0.21 0.055 250);
    --card-hi:  oklch(0.26 0.065 250);
    --line:     oklch(0.34 0.07  250);
    --txt:      oklch(0.95 0.012  80);
    --txt-dim:  oklch(0.76 0.025 250);
    --faint:    oklch(0.58 0.035 250);
    --copper:   oklch(0.68 0.20   82);
    --accent:   oklch(0.80 0.18   82);
    --amber-glow:oklch(0.84 0.20  84);
    --green:    oklch(0.70 0.14  150);
    --cyan:     oklch(0.72 0.14   82);
    --red:      oklch(0.62 0.18   25);
    --on-amber: #060810;
    --scrollbar-thumb: oklch(0.30 0.07 250);
    --body-grad-top:   oklch(0.18 0.06 250);
    --body-grad-bot:   oklch(0.08 0.03 250);
    --dialog-backdrop: oklch(0.08 0.03 250 / 0.65);
    --pre-bg:          oklch(0.15 0.05 250);
    --chart-grid:      #1a2040;
    --chart-green:     #60b060;
    --chart-cyan:      #d0a020;
    --chart-label:     #807060;
  }

  /* Themed scrollbars — match the active theme instead of the OS default grey. */
  * { scrollbar-width: thin; scrollbar-color: var(--line) transparent; }
  *::-webkit-scrollbar { width: 8px; height: 8px; }
  *::-webkit-scrollbar-track { background: transparent; }
  *::-webkit-scrollbar-thumb {
    background: var(--scrollbar-thumb); border-radius: 8px;
    border: 2px solid transparent; background-clip: padding-box;
  }
  *::-webkit-scrollbar-thumb:hover { background: var(--copper); background-clip: padding-box; }
  *::-webkit-scrollbar-corner { background: transparent; }
  body { font-family: var(--font-ui);
         background:
           radial-gradient(120% 80% at 50% -10%, var(--body-grad-top), var(--body-grad-bot)),
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
    text-shadow: 0 0 12px color-mix(in oklch, var(--amber-glow) 50%, transparent); }
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
  dialog::backdrop { background: var(--dialog-backdrop); }
  pre { background: var(--pre-bg) !important; color: var(--txt-dim); border-color: var(--line) !important; }
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
/* GEN:BOARD-THEMES START */
  html[data-theme="weatherstar"] {
    --bg: oklch(0.18 0.035 258.3); --panel: oklch(0.29 0.055 254); --card: oklch(0.37 0.065 251); --card-hi: oklch(0.7 0.19 50);
    --line: oklch(0.7 0.19 50); --txt: oklch(1 0 0); --txt-dim: oklch(0.82 0.02 250); --faint: oklch(0.7 0.19 50);
    --copper: oklch(0.9 0.17 92); --accent: oklch(0.78 0.18 55); --amber-glow: oklch(0.91 0.18 96);
    --green: oklch(0.8 0.17 62); --cyan: oklch(0.78 0.18 55); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.7 0.19 50); --body-grad-top: oklch(0.29 0.055 254); --body-grad-bot: oklch(0.18 0.035 258.3);
    --dialog-backdrop: oklch(0.18 0.035 258.3 / 0.6); --pre-bg: oklch(0.29 0.055 254);
    --chart-grid: #F77200; --chart-green: #FFA12F; --chart-cyan: #FF932A; --chart-label: #BBC5D1;
  }
  html[data-theme="retro-purple"] {
    --bg: oklch(0.142 0.066 295.8); --panel: oklch(0.276 0.09 296.3); --card: oklch(0.374 0.127 297.6); --card-hi: oklch(0.593 0.273 328.4);
    --line: oklch(0.593 0.273 328.4); --txt: oklch(1 0 0); --txt-dim: oklch(0.757 0 0); --faint: oklch(0.593 0.273 328.4);
    --copper: oklch(0.905 0.155 194.8); --accent: oklch(0.702 0.322 328.4); --amber-glow: oklch(0.919 0.129 195.2);
    --green: oklch(0.757 0.251 327.7); --cyan: oklch(0.702 0.322 328.4); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.593 0.273 328.4); --body-grad-top: oklch(0.276 0.09 296.3); --body-grad-bot: oklch(0.142 0.066 295.8);
    --dialog-backdrop: oklch(0.142 0.066 295.8 / 0.6); --pre-bg: oklch(0.276 0.09 296.3);
    --chart-grid: #CC00CC; --chart-green: #FF66FF; --chart-cyan: #FF02FF; --chart-label: #B0B0B0;
  }
  html[data-theme="inverted"] {
    --bg: oklch(0.939 0.027 78.2); --panel: oklch(0.85 0.059 75.3); --card: oklch(0.754 0.085 67.1); --card-hi: oklch(0.551 0.162 251.4);
    --line: oklch(0.551 0.162 251.4); --txt: oklch(0.218 0 0); --txt-dim: oklch(0.409 0 0); --faint: oklch(0.551 0.162 251.4);
    --copper: oklch(0.485 0.291 264.1); --accent: oklch(0.658 0.189 250.5); --amber-glow: oklch(0.533 0.26 262.6);
    --green: oklch(0.713 0.16 245.1); --cyan: oklch(0.658 0.189 250.5); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.551 0.162 251.4); --body-grad-top: oklch(0.85 0.059 75.3); --body-grad-bot: oklch(0.939 0.027 78.2);
    --dialog-backdrop: oklch(0.939 0.027 78.2 / 0.6); --pre-bg: oklch(0.85 0.059 75.3);
    --chart-grid: #0073CC; --chart-green: #34AAFF; --chart-cyan: #0094FF; --chart-label: #4A4A4A;
  }
  html[data-theme="zombie"] {
    --bg: oklch(0.198 0.038 144); --panel: oklch(0.278 0.045 144.3); --card: oklch(0.375 0.066 144.1); --card-hi: oklch(0.732 0.249 142.5);
    --line: oklch(0.732 0.249 142.5); --txt: oklch(1 0 0); --txt-dim: oklch(0.757 0 0); --faint: oklch(0.732 0.249 142.5);
    --copper: oklch(0.882 0.199 160.1); --accent: oklch(0.866 0.295 142.5); --amber-glow: oklch(0.905 0.149 167.7);
    --green: oklch(0.887 0.234 143.3); --cyan: oklch(0.866 0.295 142.5); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.732 0.249 142.5); --body-grad-top: oklch(0.278 0.045 144.3); --body-grad-bot: oklch(0.198 0.038 144);
    --dialog-backdrop: oklch(0.198 0.038 144 / 0.6); --pre-bg: oklch(0.278 0.045 144.3);
    --chart-grid: #00CC00; --chart-green: #66FF66; --chart-cyan: #00FF00; --chart-label: #B0B0B0;
  }
  html[data-theme="code-fall"] {
    --bg: oklch(0.161 0.013 144.9); --panel: oklch(0.201 0.031 144.3); --card: oklch(0.253 0.045 144.1); --card-hi: oklch(0.732 0.249 142.5);
    --line: oklch(0.732 0.249 142.5); --txt: oklch(1 0 0); --txt-dim: oklch(0.757 0 0); --faint: oklch(0.732 0.249 142.5);
    --copper: oklch(0.85 0.20 166); --accent: oklch(0.82 0.16 192); --amber-glow: oklch(0.90 0.16 110);
    --green: oklch(0.90 0.24 135); --cyan: oklch(0.82 0.16 192); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.732 0.249 142.5); --body-grad-top: oklch(0.201 0.031 144.3); --body-grad-bot: oklch(0.161 0.013 144.9);
    --dialog-backdrop: oklch(0.161 0.013 144.9 / 0.6); --pre-bg: oklch(0.201 0.031 144.3);
    --chart-grid: #00CC00; --chart-green: #92FF3E; --chart-cyan: #00E4DF; --chart-label: #B0B0B0;
  }
  html[data-theme="winter"] {
    --bg: oklch(0.2 0.04 258.3); --panel: oklch(0.342 0.071 251.8); --card: oklch(0.459 0.097 251.6); --card-hi: oklch(0.641 0.129 231.1);
    --line: oklch(0.641 0.129 231.1); --txt: oklch(1 0 0); --txt-dim: oklch(0.757 0 0); --faint: oklch(0.641 0.129 231.1);
    --copper: oklch(0.904 0.162 144.1); --accent: oklch(0.755 0.153 231.6); --amber-glow: oklch(0.929 0.131 144.4);
    --green: oklch(0.815 0.082 225.8); --cyan: oklch(0.755 0.153 231.6); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.641 0.129 231.1); --body-grad-top: oklch(0.342 0.071 251.8); --body-grad-bot: oklch(0.2 0.04 258.3);
    --dialog-backdrop: oklch(0.2 0.04 258.3 / 0.6); --pre-bg: oklch(0.342 0.071 251.8);
    --chart-grid: #0299CC; --chart-green: #87CEEB; --chart-cyan: #02BFFF; --chart-label: #B0B0B0;
  }
  html[data-theme="code-red"] {
    --bg: oklch(0.151 0.009 18.2); --panel: oklch(0.178 0.023 19.5); --card: oklch(0.22 0.033 20.1); --card-hi: oklch(0.531 0.218 29.2);
    --line: oklch(0.531 0.218 29.2); --txt: oklch(1 0 0); --txt-dim: oklch(0.757 0 0); --faint: oklch(0.531 0.218 29.2);
    --copper: oklch(0.646 0.241 32.6); --accent: oklch(0.628 0.258 29.2); --amber-glow: oklch(0.698 0.197 38);
    --green: oklch(0.704 0.187 23.2); --cyan: oklch(0.628 0.258 29.2); --red: oklch(0.66 0.17 25); --on-amber: #0A0A0A;
    --scrollbar-thumb: oklch(0.531 0.218 29.2); --body-grad-top: oklch(0.178 0.023 19.5); --body-grad-bot: oklch(0.151 0.009 18.2);
    --dialog-backdrop: oklch(0.151 0.009 18.2 / 0.6); --pre-bg: oklch(0.178 0.023 19.5);
    --chart-grid: #CC0000; --chart-green: #FF6666; --chart-cyan: #FF0000; --chart-label: #B0B0B0;
  }
  /* GEN:BOARD-THEMES END */
  </style>
</head><body class="{{BODY_CLASS}}">
<header class="px-6 py-3 flex items-center gap-3 flex-wrap" style="background:var(--panel);border-bottom:1px solid var(--line)">
  <span class="logo-mark">hive</span>
  <h1 class="text-base font-semibold" style="color:var(--txt);letter-spacing:.02em">Crew Board</h1>
  <span id="badge" class="chip hidden num" style="background:color-mix(in oklch,var(--accent) 14%,transparent);color:var(--accent);border:1px solid color-mix(in oklch,var(--accent) 40%,transparent)"></span>
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
<div id="pauseBanner" class="hidden px-6 py-2 text-sm font-semibold" style="background:color-mix(in oklch,var(--accent) 10%,transparent);border-bottom:1px solid var(--accent);color:var(--accent)">
  PAUSED: dispatcher will not start new work. In-flight tasks finish; reaper still runs.
</div>
<div id="nowBuilding" class="px-6 py-2 hidden" style="background:color-mix(in oklch,var(--copper) 7%,transparent);border-bottom:1px solid var(--line)"></div>
<main id="view-board" class="p-4 grid grid-cols-5 gap-3"></main>
<main id="view-stats" class="p-6 hidden"></main>

<dialog id="dlg" class="p-0 rounded-md w-[640px] max-w-full"></dialog>

<script>
// CP3: 5-lane board. backlog folds into ready, qa folds into review — those
// two still exist internally (backlog = a holding state, qa = the auto-verify
// gate where claude writes tests) but display in the merged lane so nothing is
// stranded and the pipeline is unchanged.
const COLUMNS = ["proposed","ready","in_progress","review","done"];
function _laneOf(status){ return status==='backlog'?'ready' : status==='qa'?'review' : status; }
const STATE = {tasks: [], projects: [], pending_approvals: [], lane_models: {}};
let MODELS = [];   // ollama-installed model names, fetched once for the lane picker
let SOCK = null;
let TAB = 'board';
let FILTER_PROJ = '';   // '' = all projects
let FILTER_Q = '';      // search query (lowercased)
// Embed mode: the wallpaper dashboard frames /board?embed=1&project=<slug> to
// scope the board to its active project. Seed FILTER_PROJ from the URL so the
// embedded board filters to that project on load (toolbar dropdown, hidden in
// embed, still drives FILTER_PROJ when standalone). Re-pointing the iframe src
// reloads this page → re-reads the param → re-filters. '' / missing = all.
try { FILTER_PROJ = new URLSearchParams(location.search).get('project') || ''; } catch (e) { /* noop */ }
let NOTIFY = false;     // browser notifications on review_ready/escalated
let BOARD_PAUSED = false; // mirrors board.paused from /state
const SEEN_EVENTS = new Set();
// Per-process session token — sent as X-Board-Token on every mutation. The
// gateway regenerates _BOARD_TOKEN on every restart, so the page-load value goes
// stale across a restart and mutations (assign/move/delete…) 401. Keep it live:
// re-fetch from /board/session-token on load + on every poll, so an open page
// self-heals within one cycle instead of silently failing until a manual reload.
let BOARD_TOKEN = document.querySelector('meta[name="board-token"]').content;
async function _refreshBoardToken() {
  try {
    const r = await fetch('/board/session-token');
    if (r.ok) { const j = await r.json(); if (j && j.token) BOARD_TOKEN = j.token; }
  } catch (e) { /* keep the current token */ }
}
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
      <td class="py-1 pr-3 num text-xs">${escapeHtml(p.slug)}</td>
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
  // Resolve CSS custom properties into Canvas-compatible hex via computed style.
  const cs = getComputedStyle(document.documentElement);
  const C_GRID  = (cs.getPropertyValue('--chart-grid')  || '#3a342c').trim();
  const C_GREEN  = (cs.getPropertyValue('--chart-green') || '#5cc870').trim();
  const C_CYAN   = (cs.getPropertyValue('--chart-cyan')  || '#60c8c8').trim();
  const C_LABEL  = (cs.getPropertyValue('--chart-label') || '#8a8780').trim();
  // Background grid lines
  ctx.strokeStyle = C_GRID;
  ctx.lineWidth = 1;
  for (let g = 0; g <= 3; g++) {
    const y = PAD.t + (g / 3) * ch;
    ctx.beginPath(); ctx.moveTo(PAD.l, y); ctx.lineTo(PAD.l + cw, y); ctx.stroke();
  }
  // Draw a filled series
  function drawSeries(color, getter) {
    ctx.beginPath();
    ctx.moveTo(px(0), py(getter(data[0])));
    for (let i = 1; i < n; i++) ctx.lineTo(px(i), py(getter(data[i])));
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.lineTo(px(n-1), PAD.t + ch);
    ctx.lineTo(PAD.l, PAD.t + ch);
    ctx.closePath();
    // Fill with 12% opacity of the stroke color
    ctx.fillStyle = color + '1f'; ctx.fill();
  }
  drawSeries(C_GREEN, d => d.hive);
  drawSeries(C_CYAN,  d => d.claude);
  // X-axis date labels (first, mid, last)
  ctx.fillStyle = C_LABEL; ctx.font = '9px "JetBrains Mono",ui-monospace,monospace'; ctx.textAlign = 'center';
  const labelIdx = [0, Math.floor((n-1)/2), n-1];
  for (const i of labelIdx) ctx.fillText(data[i].date.slice(5), px(i), H - 5);
  // Legend
  const last = data[data.length-1];
  const fmtK = v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e3 ? (v/1e3).toFixed(1)+'k' : String(v);
  legend.innerHTML =
    `<span class="num" style="color:${C_GREEN}">&#9632; hive ${fmtK(last.hive)} (latest day)</span>` +
    `<span class="num" style="color:${C_CYAN}">&#9632; claude ${fmtK(last.claude)} (latest day)</span>`;
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
  await _refreshBoardToken();   // keep the mutation token live across gateway restarts
  const r = await fetch('/board/state'); const j = await r.json();
  STATE.tasks = j.tasks; STATE.projects = j.projects; STATE.pending_approvals = j.pending_approvals;
  STATE.lane_models = j.lane_models || {};
  _applyPaused(!!j.paused);
  render();
}
async function loadModels() {
  try {
    const r = await fetch('/board/models'); if (!r.ok) return;
    MODELS = (await r.json()).models || [];
    render();   // models arrive after the first render — repopulate the lane picker
  } catch (e) { /* leave MODELS empty -> picker still shows current + Default */ }
}
async function setLaneModel(status, model) {
  STATE.lane_models[status] = model;   // optimistic; render keeps the selection
  try {
    const r = await fetch('/board/lane-model', {
      method:'POST', headers:_mutHeaders(),
      body: JSON.stringify({status, model}),
    });
    if (!r.ok) alert('set lane model failed: ' + (await r.text()));
  } catch (e) { alert('set lane model error: ' + e); }
}
function _applyPaused(paused) {
  BOARD_PAUSED = paused;
  const btn = document.getElementById('pauseBtn');
  const banner = document.getElementById('pauseBanner');
  if (paused) {
    if (btn) { btn.textContent = '▶ Resume'; btn.style.background='color-mix(in oklch,var(--accent) 12%,transparent)'; btn.style.color='var(--accent)'; btn.style.borderColor='var(--accent)'; }
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
      <span style="color:var(--txt-dim)" class="text-xs num">${escapeHtml(t.project_slug)} · ${t.agent_turns||0} turns · ${since}</span>
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
// Per-lane model dropdown. Only the in_progress lane runs a model (the build),
// so that's the only column that gets a picker. Empty value = pipeline default.
function _laneModelPicker(col) {
  if (col !== 'in_progress') return '';
  const cur = STATE.lane_models[col] || '';
  const opts = [...MODELS];
  if (cur && !opts.includes(cur)) opts.unshift(cur);   // keep a since-removed model visible
  const optHtml = ['<option value=""' + (cur ? '' : ' selected') + '>Default model</option>']
    .concat(opts.map(m => `<option value="${escapeHtml(m)}"${m===cur?' selected':''}>${escapeHtml(m)}</option>`))
    .join('');
  return `<select title="build model for this lane" onchange="setLaneModel('${col}', this.value)"
    style="background:var(--card-hi);color:var(--txt-dim);border:1px solid var(--line);border-radius:5px;font-size:10px;padding:1px 4px;max-width:140px">${optHtml}</select>`;
}

function render() {
  _syncProjFilter();
  _nowBuilding();
  const cont = document.getElementById('view-board');
  cont.innerHTML = '';
  for (const col of COLUMNS) {
    const tasks = STATE.tasks.filter(t => _laneOf(t.status) === col && _visible(t));
    const div = document.createElement('div');
    div.className = 'col p-2';
    div.innerHTML = `
      <div class="flex items-center justify-between mb-2 px-1" style="border-bottom:1px solid var(--line);padding-bottom:6px">
        <div style="font-size:11px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)">${col.replace('_',' ')}</div>
        <div class="flex items-center gap-2">${_laneModelPicker(col)}<div class="text-xs num" style="color:var(--faint)">${tasks.length}</div></div>
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

// CP1: live AI-thoughts panel + steer box, shown on in_progress cards. The
// thoughts stream in live over the board WebSocket (task_progress.thought);
// the steer box POSTs an owner nudge the hive loop injects on its next turn.
function _thoughtsHtml(t) {
  if (t.status !== 'in_progress') return '';
  const th = t.live_thoughts || [];
  const rows = th.slice(-6).map(x => {
    const why = escapeHtml(x.th || '');
    const act = escapeHtml(x.a || '');
    const body = why
      ? (why + (act ? ` <span style="color:var(--faint);opacity:.7">· ${act}</span>` : ''))
      : act;
    return `<div class="ai-th" style="margin-bottom:3px"><span style="color:var(--faint)">t${x.t}</span> ${body}</div>`;
  }).join('') || '<div class="ai-th" style="opacity:.55">waiting for the AI…</div>';
  return `<div onclick="event.stopPropagation()" style="margin-top:6px;border:1px solid var(--line);border-radius:6px;background:var(--card-hi);overflow:hidden">
    <div style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);padding:3px 7px;border-bottom:1px solid var(--line)">🧠 AI thinking</div>
    <div id="ai-thoughts-${t.slug}" style="max-height:120px;overflow-y:auto;padding:4px 7px;font-size:11px;line-height:1.35;color:var(--txt-dim);font-family:var(--font-mono,monospace)">${rows}</div>
    <div style="display:flex;gap:4px;padding:4px 6px;border-top:1px solid var(--line)">
      <input id="steer-${t.slug}" placeholder="steer the AI…" onkeydown="if(event.key==='Enter'){event.stopPropagation();sendSteer('${t.slug}')}" onclick="event.stopPropagation()" style="flex:1;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:4px;font-size:11px;padding:2px 6px" />
      <button onclick="event.stopPropagation();sendSteer('${t.slug}')" title="send guidance to the AI" style="background:var(--accent);color:var(--on-amber);border:none;border-radius:4px;padding:2px 9px;cursor:pointer;font-weight:600">↳</button>
    </div>
  </div>`;
}
async function sendSteer(slug) {
  const inp = document.getElementById('steer-' + slug);
  const msg = ((inp && inp.value) || '').trim();
  if (!msg) return;
  if (inp) inp.value = '';
  try {
    const r = await fetch(`/board/tasks/${slug}/steer`, {method:'POST', headers:_mutHeaders(), body: JSON.stringify({message: msg})});
    const tb = document.getElementById('ai-thoughts-' + slug);
    if (tb && r.ok) { const d=document.createElement('div'); d.className='ai-th'; d.style.color='var(--accent)'; d.textContent='↳ you: '+msg; tb.appendChild(d); tb.scrollTop=tb.scrollHeight; }
  } catch(e) {}
}
// CP2: master-plan card (kind='plan', shown in Proposed) — goal + Karpathy
// checkpoints with check-offs + approve(breakout)/reject/request-changes.
function _planCardHtml(t) {
  const p = t.plan_spec || {};
  const steps = (p.steps || []).map((s,i) => `
    <div style="padding:4px 0;border-top:1px solid var(--line)">
      <div style="font-weight:600;color:var(--txt);font-size:12px">${i+1}. ${escapeHtml(s.title||'')}</div>
      ${s.verify?`<div style="font-size:10px;color:var(--faint)">verify: ${escapeHtml(s.verify)}</div>`:''}
      ${(s.criteria||[]).map(c=>`<div style="font-size:11px;color:var(--txt-dim)">☐ ${escapeHtml(c)}</div>`).join('')}
    </div>`).join('');
  const q = (p.open_questions||[]).length
    ? `<div style="margin:4px 0;font-size:11px;color:var(--amber)">Open Qs: ${(p.open_questions).map(escapeHtml).join(' · ')}</div>` : '';
  return `<div class="card p-2" style="border:1px solid var(--accent)">
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
      <span style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);font-weight:700">📋 MASTER PLAN</span>
      <span class="num" style="margin-left:auto;color:var(--txt-dim);font-size:10px;font-weight:600">${t.slug}</span>
    </div>
    <div style="font-size:12px;color:var(--txt);margin-bottom:2px">${escapeHtml(p.goal||t.title)}</div>
    ${q}
    <div>${steps || '<div style="color:var(--faint);font-size:11px">no steps drafted</div>'}</div>
    <div style="display:flex;gap:4px;margin-top:6px;flex-wrap:wrap">
      <button onclick="approvePlan('${t.slug}')" style="background:color-mix(in oklch,var(--green) 16%,transparent);color:var(--green);border:1px solid color-mix(in oklch,var(--green) 45%,transparent);border-radius:5px;padding:3px 9px;cursor:pointer;font-size:11px;font-weight:600">✓ Approve → breakout</button>
      <button onclick="rejectPlan('${t.slug}')" style="background:color-mix(in oklch,var(--red) 14%,transparent);color:var(--red);border:1px solid color-mix(in oklch,var(--red) 40%,transparent);border-radius:5px;padding:3px 9px;cursor:pointer;font-size:11px">✗ Reject</button>
      <button onclick="document.getElementById('pf-${t.slug}').style.display='flex'" style="background:var(--card-hi);color:var(--txt-dim);border:1px solid var(--line);border-radius:5px;padding:3px 9px;cursor:pointer;font-size:11px">✎ Request changes</button>
    </div>
    <div id="pf-${t.slug}" style="display:none;gap:4px;margin-top:5px">
      <input id="pfi-${t.slug}" placeholder="what to change…" onkeydown="if(event.key==='Enter')requestPlanChanges('${t.slug}')" style="flex:1;background:var(--bg);color:var(--txt);border:1px solid var(--line);border-radius:4px;font-size:11px;padding:3px 6px" />
      <button onclick="requestPlanChanges('${t.slug}')" title="re-draft from your feedback" style="background:var(--accent);color:var(--on-amber);border:none;border-radius:4px;padding:3px 9px;cursor:pointer;font-weight:600">↻</button>
    </div>
  </div>`;
}
async function approvePlan(slug) {
  if (!confirm('Approve this plan and break it into tickets?')) return;
  const r = await fetch(`/board/plans/${slug}/approve`, {method:'POST', headers:_mutHeaders()});
  if (r.ok) { const d=await r.json(); await loadState(); alert(`Created ${(d.created||[]).length} tickets from the plan.`); }
  else alert('approve failed: ' + (await r.text()));
}
async function rejectPlan(slug) {
  if (!confirm('Reject + archive this plan?')) return;
  const r = await fetch(`/board/plans/${slug}/reject`, {method:'POST', headers:_mutHeaders()});
  if (r.ok) loadState(); else alert('reject failed: ' + (await r.text()));
}
async function requestPlanChanges(slug) {
  const inp = document.getElementById('pfi-' + slug);
  const fb = ((inp && inp.value) || '').trim();
  if (!fb) return;
  if (inp) inp.value = 'redrafting…';
  const r = await fetch(`/board/plans/${slug}/request-changes`, {method:'POST', headers:_mutHeaders(), body: JSON.stringify({feedback: fb})});
  if (r.ok) loadState(); else alert('re-draft failed: ' + (await r.text()));
}
// #210: propose skill improvements (new / update existing) from a reviewed task.
async function suggestSkills(slug, btn) {
  if (btn) { btn.textContent='analyzing…'; btn.disabled=true; }
  try {
    const r = await fetch(`/board/tasks/${slug}/suggest-skills`, {method:'POST', headers:_mutHeaders()});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail||'failed');
    const n = (d.created||[]).length;
    if (btn) btn.textContent = n ? `✓ ${n} skill idea(s) → Proposed` : '— no skill ideas';
    if (n) loadState();
  } catch(e) { if (btn){ btn.textContent='💡 suggest skills'; btn.disabled=false; } alert('skills suggest failed: '+(e.message||e)); }
}
function taskCard(t) {
  if (t.kind === 'plan') return _planCardHtml(t);
  const prio = {
    high:'background:color-mix(in oklch,var(--red) 16%,transparent);color:var(--red)',
    medium:'background:var(--card-hi);color:var(--txt-dim)',
    low:'background:var(--card-hi);color:var(--faint)'
  }[t.priority] || '';
  const assignee = t.assignee !== 'none' ? `<span class="chip" style="background:color-mix(in oklch,var(--copper) 16%,transparent);color:var(--copper)">${t.assignee}</span>` : '';
  const proj = `<span class="text-xs num" style="color:var(--txt-dim)">${escapeHtml(t.project_slug)}</span>`;
  const checked = (t.acceptance_criteria || []).filter(c=>c.checked).length;
  const total = (t.acceptance_criteria || []).length;
  const progress = total ? `<span class="text-xs num" style="color:var(--txt-dim)">${checked}/${total}</span>` : '';
  const htok = t.hive_tokens ? `<span class="chip num tok-hive" style="background:color-mix(in oklch,var(--green) 12%,transparent)" title="hive tokens">H ${fmtTokens(t.hive_tokens)}</span>` : '';
  const ctok = t.claude_tokens ? `<span class="chip num tok-claude" style="background:color-mix(in oklch,var(--cyan) 12%,transparent)" title="claude tokens">C ${fmtTokens(t.claude_tokens)}</span>` : '';
  // Live "now doing" line — only while in_progress, so you can watch
  // the hive work turn by turn.
  const live = (t.status === 'in_progress' && t.last_action)
    ? `<div class="liveact" id="live-${t.slug}"><span class="livedot"></span>${escapeHtml(t.last_action)}</div>`
    : '';
  // #198: last-agent handoff note — shown on finished/idle cards (live takes
  // priority while in_progress) so a glance tells you what the last AI did.
  const note = (t.status !== 'in_progress' && t.last_summary)
    ? `<div class="liveact" style="opacity:.7" title="${escapeHtml(t.last_summary)}">📝 ${escapeHtml(t.last_summary.slice(0,90))}${t.last_summary.length>90?'…':''}</div>`
    : '';
  const rate = (t.status === 'in_progress')
    ? `<span class="chip num" style="background:var(--card-hi);color:var(--txt-dim)" title="turns, elapsed">${t.agent_turns||0}t · ${t.updated_at?_ago(t.updated_at):''}</span>`
    : '';
  // #172: blocked badge — count depends_on tasks not yet done/archived.
  const blockers = (t.depends_on || []).filter(d => {
    const dep = STATE.tasks.find(x => x.slug === d);
    return dep && dep.status !== 'done' && dep.status !== 'archived';
  });
  const blocked = blockers.length
    ? `<span class="chip" style="background:color-mix(in oklch,var(--red) 16%,transparent);color:var(--red)" title="blocked by ${blockers.join(', ')} — won't start until they're done">🔒 ${blockers.length}</span>`
    : '';
  return `<div class="card p-2 cursor-pointer${blockers.length?' card-blocked':''}" onclick='openDetail("${t.slug}")'>
    <div class="flex items-start justify-between gap-2 mb-1">
      <div class="text-sm font-medium" style="color:var(--txt)">${escapeHtml(t.title)}</div>
      <div class="flex items-center gap-1">
        <span class="num" style="color:var(--txt);font-weight:600;font-size:11px;letter-spacing:.02em">${t.slug}</span>
        <button onclick="event.stopPropagation();deleteTask('${t.slug}')" title="Delete permanently" style="color:var(--faint);background:none;border:none;cursor:pointer;padding:0 2px;font-size:13px;line-height:1" onmouseover="this.style.color='var(--red)'" onmouseout="this.style.color='var(--faint)'">🗑</button>
      </div>
    </div>
    <div class="flex items-center gap-1.5 flex-wrap">
      ${proj}
      <span class="chip" style="${prio}">${t.priority}</span>
      ${t.status==='qa'?`<span class="chip" style="background:color-mix(in oklch,var(--cyan) 14%,transparent);color:var(--cyan)" title="in the QA / auto-verify gate">⚙ QA</span>`:''}
      ${assignee}
      ${progress}
      ${htok}${ctok}
      ${t.smoke_cmd?`<span class="chip" style="${t.smoke_ok===true?'background:color-mix(in oklch,var(--green) 12%,transparent);color:var(--green)':t.smoke_ok===false?'background:color-mix(in oklch,var(--red) 16%,transparent);color:var(--red)':'background:var(--card-hi);color:var(--txt-dim)'}" title="smoke gate">⚙${t.smoke_ok===true?'✓':t.smoke_ok===false?'✗':''}</span>`:''}
      ${t.review_by?`<span class="chip" style="background:color-mix(in oklch,var(--accent) 12%,transparent);color:var(--accent)" title="reviewer">👁</span>`:''}
      ${blocked}
      ${rate}
    </div>
    ${(t.status==='review'||t.status==='qa')?`<div style="margin-top:4px"><button onclick="event.stopPropagation();suggestSkills('${t.slug}',this)" style="background:var(--card-hi);border:1px solid var(--line);border-radius:5px;color:var(--txt-dim);cursor:pointer;font-size:10px;padding:2px 7px" title="propose skill improvements from this work → Proposed">💡 suggest skills</button></div>`:''}
    ${live}${note}${_thoughtsHtml(t)}
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
          ${(t.status!=='done'&&t.status!=='archived')?`<button onclick="unstuckTask('${t.slug}')" title="Bring Claude in to diagnose and push this ticket along" class="text-xs rounded px-2 py-0.5" style="border:1px solid color-mix(in oklch,var(--cyan) 45%,transparent);background:color-mix(in oklch,var(--cyan) 12%,transparent);color:var(--cyan)">🩹 Unstuck</button>`:''}
          <button onclick="document.getElementById('dlg').close();deleteTask('${t.slug}')" title="Delete permanently" class="text-xs rounded px-2 py-0.5" style="border:1px solid color-mix(in oklch,var(--red) 40%,transparent);background:color-mix(in oklch,var(--red) 12%,transparent);color:var(--red)">🗑 Delete</button>
          <button onclick="document.getElementById('dlg').close()" style="color:var(--txt-dim)">✕</button>
        </div>
      </div>
      <div class="text-sm mb-2" style="color:var(--txt-dim)">${escapeHtml(t.project_slug)} · status ${t.status} · assignee ${t.assignee}${t.attempt_count?` · attempt ${t.attempt_count}`:''}</div>
      ${live}
      ${t.last_summary?`<div class="mb-2 p-2 rounded" style="background:var(--card-hi);border:1px solid var(--line)"><div class="text-xs mb-1" style="color:var(--faint)">📝 LAST AGENT NOTE${t.last_summary_by?` · ${escapeHtml(t.last_summary_by)}`:''}${t.last_summary_at?` · ${escapeHtml(t.last_summary_at)} UTC`:''}</div><div class="text-sm whitespace-pre-wrap" style="color:var(--txt)">${escapeHtml(t.last_summary)}</div></div>`:''}
      <div class="flex flex-wrap gap-1 mb-2">
        ${t.review_by?`<span class="chip" style="background:color-mix(in oklch,var(--accent) 12%,transparent);color:var(--accent)">review: ${escapeHtml(t.review_by)}</span>`:''}
        ${t.polish_iters?`<span class="chip num" style="background:color-mix(in oklch,var(--accent) 12%,transparent);color:var(--accent)">polish ×${t.polish_iters}</span>`:''}
        ${t.smoke_cmd?`<span class="chip" style="${t.smoke_ok===true?'background:color-mix(in oklch,var(--green) 12%,transparent);color:var(--green)':t.smoke_ok===false?'background:color-mix(in oklch,var(--red) 16%,transparent);color:var(--red)':'background:var(--card-hi);color:var(--txt-dim)'}">smoke ${t.smoke_ok===true?'✓':t.smoke_ok===false?'✗':'·'}</span>`:''}
        ${(t.depends_on||[]).length?`<span class="chip num" style="background:var(--card-hi);color:var(--txt-dim)">deps: ${t.depends_on.length}</span>`:''}
        ${t.hive_tokens?`<span class="chip num tok-hive" style="background:color-mix(in oklch,var(--green) 12%,transparent)">hive ${fmtTokens(t.hive_tokens)} tok</span>`:''}
        ${t.claude_tokens?`<span class="chip num tok-claude" style="background:color-mix(in oklch,var(--cyan) 12%,transparent)">claude ${fmtTokens(t.claude_tokens)} tok</span>`:''}
      </div>
      <pre class="text-sm whitespace-pre-wrap p-2 rounded max-h-40 overflow-auto" style="background:var(--pre-bg);color:var(--txt);border:1px solid var(--line)">${escapeHtml(t.body || '(no body)')}</pre>
      <h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Acceptance criteria</h3>
      <ul class="space-y-1 text-sm" style="color:var(--txt)">
        ${(t.acceptance_criteria || []).map((c,i) => `
          <li><label class="flex items-start gap-2"><input type="checkbox" ${c.checked?'checked':''} onchange="toggleCriterion('${t.slug}',${i},this.checked)" /><span>${escapeHtml(c.text)}</span></label></li>
        `).join('')}
      </ul>
      ${(t.files_of_interest || []).length ? `<h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Files</h3><ul class="text-xs" style="color:var(--txt-dim)">${(t.files_of_interest).map(f=>`<li><code>${escapeHtml(f)}</code></li>`).join('')}</ul>` : ''}
      ${Object.keys(t.verify_results||{}).length ? `<h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Verify</h3><pre class="text-xs p-2 rounded max-h-32 overflow-auto" style="background:var(--pre-bg);color:var(--txt);border:1px solid var(--line)">${escapeHtml(JSON.stringify(t.verify_results, null, 2))}</pre>` : ''}
      <h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Transcript <span class="text-xs" style="color:var(--txt-dim)">(agent turns)</span></h3>
      <div id="transcript" class="text-xs p-2 rounded max-h-48 overflow-auto num" style="background:var(--pre-bg);border:1px solid var(--line);color:var(--txt-dim)">loading…</div>
      <h3 class="font-medium mt-3 mb-1" style="color:var(--txt)">Diff <span class="text-xs" style="color:var(--txt-dim)">(this task's commit)</span></h3>
      <pre id="diff" class="text-xs p-2 rounded max-h-56 overflow-auto" style="background:var(--pre-bg);border:1px solid var(--line);color:var(--txt-dim);white-space:pre-wrap">loading…</pre>
      ${t.status==='review'?`<div class="mt-3 flex gap-2">
        <button onclick="moveTask('${t.slug}','done')" class="rounded px-3 py-1" style="background:color-mix(in oklch,var(--green) 14%,transparent);color:var(--green);border:1px solid color-mix(in oklch,var(--green) 40%,transparent)">✓ Approve, done</button>
        <button onclick="moveTask('${t.slug}','in_progress')" class="rounded px-3 py-1" style="background:color-mix(in oklch,var(--red) 14%,transparent);color:var(--red);border:1px solid color-mix(in oklch,var(--red) 40%,transparent)">✗ Reject, rework</button>
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
// Project list sort: 'modified' (last worked on, newest first) | 'name'.
// Lives outside openProjects so the choice survives a re-render.
let PROJ_SORT = 'modified';
function _projAgo(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso); if (isNaN(t)) return '—';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return 'just now';
  const m = s / 60; if (m < 60) return Math.floor(m) + 'm ago';
  const h = m / 60; if (h < 24) return Math.floor(h) + 'h ago';
  const d = h / 24; if (d < 30) return Math.floor(d) + 'd ago';
  const mo = d / 30; if (mo < 12) return Math.floor(mo) + 'mo ago';
  return Math.floor(d / 365) + 'y ago';
}
function _sortedProjects() {
  const arr = [...(STATE.projects || [])];
  if (PROJ_SORT === 'name') {
    arr.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  } else {
    // Newest modified first; missing dates (-Infinity) sink to the bottom.
    arr.sort((a, b) =>
      (Date.parse(b.modified_at || '') || -Infinity) -
      (Date.parse(a.modified_at || '') || -Infinity));
  }
  return arr;
}
function setProjSort(v) { PROJ_SORT = v; openProjects(); }
function openProjects() {
  const dlg = document.getElementById('dlg');
  const sel = (v, label) =>
    `<option value="${v}"${PROJ_SORT === v ? ' selected' : ''}>${label}</option>`;
  dlg.innerHTML = `
    <div class="p-5" style="background:var(--panel);color:var(--txt)">
      <div class="flex items-center justify-between gap-2 mb-3">
        <h2 class="text-lg font-semibold" style="color:var(--txt)">Projects</h2>
        <div class="flex items-center gap-2">
          <label class="text-xs" style="color:var(--txt-dim)">Sort</label>
          <select onchange="setProjSort(this.value)" class="text-xs rounded px-1 py-0.5" style="border:1px solid var(--line);background:var(--card-hi);color:var(--txt)">
            ${sel('modified', 'Last modified')}${sel('name', 'Name')}
          </select>
          <button onclick="document.getElementById('dlg').close()" style="color:var(--txt-dim)">✕</button>
        </div>
      </div>
      <ul class="space-y-1 text-sm">
        ${_sortedProjects().map(p => `
          <li class="rounded p-2 flex items-center justify-between" style="border:1px solid var(--line);background:var(--card)">
            <div><div class="font-medium" style="color:var(--txt)">${escapeHtml(p.name)}${p.enabled?' <span class="chip" style="background:color-mix(in oklch,var(--green) 14%,transparent);color:var(--green)">on</span>':''}</div><div class="text-xs" style="color:var(--txt-dim)">${escapeHtml(p.path)}</div></div>
            <div class="flex items-center gap-2">
              <span class="text-xs" style="color:var(--txt-dim)" title="${escapeHtml(p.modified_at||'')}">${_projAgo(p.modified_at)}</span>
              <button class="text-xs rounded px-2 py-0.5" style="border:1px solid var(--line);background:var(--card-hi);color:var(--txt)" onclick="toggleProject('${p.slug}',${!p.enabled})">${p.enabled?'disable':'enable'}</button>
            </div>
          </li>
        `).join('')}
      </ul>
    </div>
  `;
  if (!dlg.open) dlg.showModal();
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
        // CP1: stream the AI's reasoning into the in-ticket thoughts panel.
        if (m.thought) {
          const tb = document.getElementById('ai-thoughts-' + m.task);
          if (tb) {
            const d = document.createElement('div'); d.className = 'ai-th'; d.style.marginBottom='3px';
            const esc = s => (s||'').replace(/[&<>]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch]));
            d.innerHTML = '<span style="color:var(--faint)">t' + (m.turn||'') + '</span> ' +
              esc(m.thought) + (m.action ? ' <span style="color:var(--faint);opacity:.7">· ' + esc(m.action) + '</span>' : '');
            tb.appendChild(d);
            while (tb.children.length > 10) tb.removeChild(tb.firstChild);
            tb.scrollTop = tb.scrollHeight;
          }
        }
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
        <option value="auto">✨ Auto — pick the right project or create one</option>
        <option value="">+ New project (auto-named)</option>
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
    const r = await fetch('/board/plans/propose', {method:'POST',headers:_mutHeaders(),
      body: JSON.stringify({goal, project_slug: project})});
    const d = await r.json();
    if (!r.ok) { plan.textContent = 'Error: ' + (d.detail||JSON.stringify(d)); btn.disabled=false; return; }
    plan.innerHTML = `<div style="color:var(--accent)">Master plan drafted (${d.steps} checkpoints). Review it in the <b>Proposed</b> lane — Approve there to break it into tickets.</div>`;
    btn.textContent = 'Done'; await loadState();
  } catch(e){ plan.textContent = 'Failed: '+e; btn.disabled=false; }
}
loadModels();
loadState();
connectEvents();
</script>
</body></html>
"""
