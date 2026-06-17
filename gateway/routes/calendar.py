"""/v1/calendar — scheduled job CRUD + upcoming list.

User-facing endpoints for the Google-Calendar-style scheduler in the
phone app.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from gateway.calendar_jobs import _RECURRENCES, _VERBS, validate_payload
from gateway.deps import require_device, require_device_or_loopback, state


router = APIRouter(prefix="/v1/calendar", tags=["calendar"])
log = logging.getLogger("gateway.calendar_route")


class CreateJob(BaseModel):
    title: str = Field(..., max_length=200)
    description: str = Field("", max_length=2000)
    scheduled_at: str         # ISO-8601 UTC
    recurrence: str = "none"
    action_verb: str
    action_payload: dict[str, Any] = Field(default_factory=dict)
    notify: bool = True


class UpdateJob(BaseModel):
    title: str | None = None
    description: str | None = None
    scheduled_at: str | None = None
    recurrence: str | None = None
    action_verb: str | None = None
    action_payload: dict[str, Any] | None = None
    notify: bool | None = None


def _store(request: Request):
    st = state(request)
    s = st.calendar_store
    if s is None:
        raise HTTPException(503, "calendar store not initialised")
    return s


def _check_rate_limit(request: Request, device_id: str) -> None:
    """Hold create/update mutators to ~30/min/device, burst 10."""
    st = state(request)
    rl = st.rate_limiter
    if rl is None:
        return
    if not rl.try_acquire(device_id, "calendar"):
        raise HTTPException(
            status_code=429,
            detail="too many calendar requests; back off",
        )


# ---------------------------------------------------------------- list


@router.get("/jobs")
def list_jobs(
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """List jobs, optionally filtered by an ISO-8601 window. Always
    scoped to the calling device (or system-wide jobs with empty
    owner_device_id)."""
    s = _store(request)
    jobs = s.list(
        owner_device_id=device.id, since=since, until=until, limit=limit,
    )
    return {"jobs": [j.to_jsonable() for j in jobs]}


@router.get("/jobs/upcoming")
def upcoming(
    n: int = Query(default=10, ge=1, le=100),
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> dict:
    """The next N scheduled jobs that haven't fired yet.

    When the caller is authenticated (token present), only jobs owned by that
    device are returned.  When the request is a loopback-exempt read (no token,
    device is None), system-wide jobs (owner_device_id = None / any) are
    returned so the local wallpaper dashboard can display the agenda.
    """
    s = _store(request)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    owner_id = device.id if device is not None else None
    jobs = [
        j for j in s.list(owner_device_id=owner_id, since=now, limit=n)
        if j.status == "scheduled"
    ][:n]
    return {"jobs": [j.to_jsonable() for j in jobs]}


# ---------------------------------------------------------------- get/create/update/delete


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    j = s.get(job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    return j.to_jsonable()


@router.post("/jobs")
def create_job(
    body: CreateJob,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    _check_rate_limit(request, device.id)
    if body.recurrence not in _RECURRENCES:
        raise HTTPException(400, f"invalid recurrence (one of {sorted(_RECURRENCES)})")
    if body.action_verb not in _VERBS:
        raise HTTPException(400, f"invalid action_verb (one of {sorted(_VERBS)})")
    err = validate_payload(body.action_verb, body.action_payload)
    if err:
        raise HTTPException(400, err)
    try:
        job = s.create(
            title=body.title,
            description=body.description,
            scheduled_at=body.scheduled_at,
            recurrence=body.recurrence,
            action_verb=body.action_verb,
            action_payload=body.action_payload,
            notify=body.notify,
            owner_device_id=device.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return job.to_jsonable()


@router.put("/jobs/{job_id}")
def update_job(
    job_id: str,
    body: UpdateJob,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    _check_rate_limit(request, device.id)
    existing = s.get(job_id)
    if existing is None:
        raise HTTPException(404, "job not found")
    if existing.owner_device_id and existing.owner_device_id != device.id:
        raise HTTPException(403, "not your job")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        updated = s.update(job_id, **fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if updated is None:
        raise HTTPException(404, "job not found")
    return updated.to_jsonable()


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    existing = s.get(job_id)
    if existing is None:
        raise HTTPException(404, "job not found")
    if existing.owner_device_id and existing.owner_device_id != device.id:
        raise HTTPException(403, "not your job")
    s.delete(job_id)
    return {"ok": True}


# ---------------------------------------------------------------- options


@router.get("/options")
def options(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Hints the app uses to populate the Create dialog dropdowns."""
    return {
        "recurrences": sorted(_RECURRENCES),
        "action_verbs": sorted(_VERBS),
    }
