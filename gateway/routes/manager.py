"""FastAPI routes for the Crew Board Manager daemon.

GET    /v1/crew/manager/status   -> {enabled, model_ready, model_id, ollama_model, current_decision, decision_count}
POST   /v1/crew/manager/toggle   -> {enabled: true/false}  (toggle ON/OFF)
POST   /v1/crew/manager/prompt   -> {goal: "NL text", project_slug?: string} (manual goal submission)
GET    /v1/crew/manager/activity -> list of recent decisions/actions
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/v1/crew/manager")
log = logging.getLogger("gateway.routes.manager")


# ─── Request models ────────────────────────────────────────────────────────

class ManagerToggleRequest(BaseModel):
    enabled: bool


class ManagerPromptRequest(BaseModel):
    goal: str
    project_slug: str | None = None


# ─── Status endpoint ──────────────────────────────────────────────────────

@router.get("/status", tags=["crew-manager"])
async def get_manager_status() -> dict[str, Any]:
    """Return daemon status for dashboard toggle button."""
    from gateway.app import app  # Lazy import to avoid circular deps
    daemon = getattr(app.state, "manager_daemon", None)
    if daemon is None:
        return {"enabled": False, "model_ready": False, "error": "daemon not initialized"}
    return daemon.status


# ─── Toggle endpoint ──────────────────────────────────────────────────────

@router.post("/toggle", tags=["crew-manager"])
async def toggle_manager(body: ManagerToggleRequest) -> dict[str, Any]:
    """Enable or disable the manager daemon."""
    from gateway.app import app
    daemon = getattr(app.state, "manager_daemon", None)
    if daemon is None:
        raise HTTPException(status_code=409, detail="daemon not initialized")

    if body.enabled:
        success = await daemon.enable()
        if not success:
            raise HTTPException(status_code=409, detail="model unavailable — cannot enable")
    else:
        daemon.disable()

    return {"enabled": daemon._enabled}  # type: ignore[attr-defined]


# ─── Manual prompt endpoint ───────────────────────────────────────────────

@router.post("/prompt", tags=["crew-manager"])
async def submit_prompt(body: ManagerPromptRequest) -> dict[str, Any]:
    """Submit a manual goal for the manager to decompose."""
    from gateway.app import app
    daemon = getattr(app.state, "manager_daemon", None)
    if daemon is None:
        raise HTTPException(status_code=409, detail="daemon not initialized")

    # Trigger asynchronous decompose — returns immediately
    try:
        asyncio_task = asyncio.create_task(
            daemon.decompose_goal(body.goal, body.project_slug or "")
        )
        return {
            "status": "processing",
            "goal": body.goal,
            "project": body.project_slug,
            "task_id": id(asyncio_task),
        }
    except Exception as e:
        log.error("manager prompt failed: %s", e)
        raise HTTPException(status_code=500, detail=f"decompose failed: {e}")


# ─── Activity endpoint ────────────────────────────────────────────────────

@router.get("/activity", tags=["crew-manager"])
async def get_activity() -> dict[str, Any]:
    """Recent decisions made by the manager daemon."""
    from gateway.app import app
    daemon = getattr(app.state, "manager_daemon", None)
    if daemon is None:
        return {"decisions": []}
    return {"decisions": daemon.activity}


# ─── Module init ──────────────────────────────────────────────────────────

import asyncio  # noqa: E402 (needed for submit_prompt)
