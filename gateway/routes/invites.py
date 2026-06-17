"""Hive node invite-code endpoints.

The owner device hits these to mint, list, or revoke invite codes for
adding hive nodes. Codes are 6-digit, single-use, TTL'd.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gateway.deps import require_device, state


log = logging.getLogger("gateway.routes.invites")

router = APIRouter(prefix="/v1/invites", tags=["hive-invites"])


class IssueInviteResponse(BaseModel):
    code: str
    expires_in_seconds: int


class InviteSummary(BaseModel):
    code: str
    created_at: float
    expires_at: float


@router.post("", response_model=IssueInviteResponse)
def issue_invite(
    request: Request, device=Depends(require_device),
) -> IssueInviteResponse:
    st = state(request)
    invite = st.node_invites.issue()
    log.info("invite issued: device=%s ttl_s=%d", device.id, int(invite.expires_at - invite.created_at))
    return IssueInviteResponse(
        code=invite.code,
        expires_in_seconds=int(invite.expires_at - invite.created_at),
    )


@router.get("", response_model=list[InviteSummary])
def list_invites(
    request: Request, device=Depends(require_device),
) -> list[InviteSummary]:
    st = state(request)
    return [
        InviteSummary(
            code=inv.code,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
        )
        for inv in st.node_invites.list_active()
    ]


@router.delete("/{code}", status_code=204)
def revoke_invite(
    code: str, request: Request, device=Depends(require_device),
) -> None:
    st = state(request)
    if not st.node_invites.revoke(code):
        raise HTTPException(status_code=404, detail="invite not found")
    log.info("invite revoked: device=%s", device.id)
