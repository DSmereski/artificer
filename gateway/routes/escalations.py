"""/v1/escalations — read+ack queue for `escalate_to_dev` notes.

Hive's `_escalate_to_dev` writes one note per dev-flagged issue under
`vault/ops/escalations/`. Without a read-side this queue grew silently
forever — closing that loop is what this module does.

Endpoints:
  GET    /v1/escalations              → open list
  GET    /v1/escalations?all=true     → include resolved
  GET    /v1/escalations/count        → open count, for the badge
  GET    /v1/escalations/{slug}       → one
  POST   /v1/escalations/{slug}/resolve   → mark done (rename to .resolved.md)
  POST   /v1/escalations/{slug}/reopen    → undo resolve

Auth: standard bearer token. We don't filter by audience here: the
escalation queue is `audience: [claude-code]`-only on the write side,
so by definition only the dev (you) is reading it. The phone app shows
the count + can resolve once you've fixed the issue from your dev seat.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from gateway.deps import require_device, require_device_or_loopback, state


router = APIRouter(prefix="/v1/escalations", tags=["escalations"])
log = logging.getLogger("gateway.escalations_route")


def _store(request: Request):
    st = state(request)
    s = st.escalation_store
    if s is None:
        raise HTTPException(503, "escalation store not initialised")
    return s


@router.get("")
def list_escalations(
    all: bool = Query(False, description="Include resolved entries"),  # noqa: A002
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> dict:
    s = _store(request)
    items = s.list(include_resolved=all)
    return {
        "open_count": s.count_open(),
        "escalations": [e.to_json() for e in items],
    }


@router.get("/count")
def count_open(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Cheap endpoint for the Activity-tab badge — just an integer."""
    s = _store(request)
    return {"open_count": s.count_open()}


@router.get("/{slug}")
def get_escalation(
    slug: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    esc = s.get(slug, include_resolved=True)
    if esc is None:
        raise HTTPException(404, f"unknown escalation: {slug}")
    return esc.to_json()


@router.post("/{slug}/resolve")
def resolve_escalation(
    slug: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    if not s.resolve(slug):
        raise HTTPException(404, f"unknown escalation: {slug}")
    return {"ok": True, "slug": slug, "open_count": s.count_open()}


@router.post("/{slug}/reopen")
def reopen_escalation(
    slug: str,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    if not s.reopen(slug):
        raise HTTPException(404, f"escalation not resolved: {slug}")
    return {"ok": True, "slug": slug, "open_count": s.count_open()}
