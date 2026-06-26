"""Pairing endpoints.

Flow:
  1. PC runs `GET /v1/pair/new` to mint a pairing code, then encodes
     {tailnet_host, port, code} into a QR.
  2. Phone scans the QR, POSTs `{code, name, platform}` to /v1/pair.
  3. Gateway claims the code, mints a device token, returns it exactly once.

Security:
  - Both endpoints are unauthenticated by necessity (the phone has no
    credentials yet) but rate-limited per client IP via the shared
    `pair_attempts` token bucket. Without the bucket, an attacker on the
    tailnet could brute-force claim a freshly-minted code by racing the
    legitimate phone, or mint codes endlessly to amplify load.
  - Freshly paired devices get a NARROW default audience
    (`["hive", "claude-code"]`), not `"all"`. A lost / stolen phone
    bearer token therefore cannot read or write content tagged for
    other audiences (e.g. `"scout"`, `"maggy"`) until the operator
    explicitly grants it. The desktop / "all"-audience seat is paired
    out-of-band, not via this flow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.auth import DeviceStore, PairingBroker, issue_token
from gateway.deps import require_device, state


log = logging.getLogger("gateway.routes.pair")

router = APIRouter(prefix="/v1", tags=["pair"])

# Default audience for a freshly paired device (phone via QR). Narrow
# enough that a lost token can't read other-bot or admin content; broad
# enough that the phone can chat with Hive and read claude-code-tagged
# content (the two audiences the user actually exercises from mobile).
_DEFAULT_PAIRED_AUDIENCE: tuple[str, ...] = ("hive", "claude-code")


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Behind a reverse proxy this needs
    X-Forwarded-For; the gateway runs on the tailnet so the socket
    IP is the real source."""
    return request.client.host if request.client else "unknown"


def _enforce_pair_rate_limit(request: Request, label: str) -> None:
    """Raise 429 if the caller has exhausted their `pair_attempts` bucket.

    Shared bucket between `/v1/pair/new` and `/v1/pair` so an attacker
    can't separately exhaust the mint side and the claim side.
    """
    st = state(request)
    ip = _client_ip(request)
    limiter = getattr(st, "rate_limiter", None)
    if limiter is None:
        return
    if not limiter.try_acquire(ip, "pair_attempts"):
        log.warning("pair %s rate limited: ip=%s", label, ip)
        raise HTTPException(
            status_code=429, detail="too many pair attempts; slow down",
        )


class NewPairingResponse(BaseModel):
    code: str
    expires_in_seconds: int


class PairRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=32)
    name: str = Field("", max_length=64)
    platform: str = Field("", max_length=32)


class PairResponse(BaseModel):
    device_id: str
    token: str
    name: str


@router.get("/pair/new", response_model=NewPairingResponse)
def new_pairing_code(request: Request) -> NewPairingResponse:
    """Mint a new pairing code.

    Rate-limited per client IP (`pair_attempts` bucket) so the tailnet
    surface can't be used to mint codes endlessly.
    """
    _enforce_pair_rate_limit(request, "new")
    st = state(request)
    code = st.pairing.issue()
    return NewPairingResponse(
        code=code,
        expires_in_seconds=st.config.pairing.code_ttl_seconds,
    )


@router.post("/pair", response_model=PairResponse)
def claim_pairing_code(body: PairRequest, request: Request) -> PairResponse:
    _enforce_pair_rate_limit(request, "claim")
    st = state(request)
    if not st.pairing.claim(body.code.strip()):
        raise HTTPException(status_code=401, detail="invalid or expired pairing code")
    token = issue_token(st.config.pairing.token_bytes)
    platform = body.platform.strip() or "unknown"
    name = body.name.strip() or f"{platform} device"
    device = st.devices.add(
        name=name, token=token, audience=_DEFAULT_PAIRED_AUDIENCE,
    )
    return PairResponse(device_id=device.id, token=token, name=device.name)


class DeviceInfo(BaseModel):
    id: str
    name: str
    created: float
    last_seen: float
    revoked: bool
    audience: list[str]


@router.get("/devices", response_model=list[DeviceInfo])
def list_devices(device=Depends(require_device), request: Request = None) -> list[DeviceInfo]:
    st = state(request)
    return [
        DeviceInfo(
            id=d.id, name=d.name, created=d.created,
            last_seen=d.last_seen, revoked=d.revoked,
            audience=list(d.audience),
        )
        for d in st.devices.list()
    ]


class PurgeResult(BaseModel):
    purged: int


# Pairing names smoke scripts and ad-hoc tests use. Anything that
# starts with one of these is a transient pairing the gateway is
# willing to auto-GC on startup AND via the /devices/purge endpoint.
# The user's real devices use stable names (`android-phone`,
# `windows-pc`) that aren't in this list.
_TRANSIENT_DEVICE_PREFIXES = [
    # Active naming conventions for new smoke scripts.
    "smoke", "vault-smoke", "vault-link-smoke", "vault-kb-smoke",
    "video-smoke", "img-smoke", "img-render", "smoke-test",
    "pytest-device", "test-",
    # Legacy / one-off names left over from older test runs.
    "prompt-battery", "research-quiz", "hive", "multi", "vault-crud",
    "verify", "apk", "log", "cal-smoke", "cal-test", "image-smoke",
    "matrix-smoke", "sc-final", "sc-test", "scout-test", "skills",
    "sysmon-smoke", "hive-only", "ws-hive", "final", "apk-check",
]

# Matches the stamp suffix that smoke / test scripts append to device names:
# a run of 8+ hex chars (the random portion), optionally followed by -<digits>.
# Examples: "smoke-deadbeef12", "hive-a1b2c3d4e5f6-2"
# The 8-hex-char minimum is deliberate: it prevents short suffixes like
# "hive-abc123" (6 hex chars) from collateral-purging real devices whose
# names happen to share a prefix with a transient smoke prefix.
_TRANSIENT_STAMP_RE = re.compile(r"^[0-9a-f]{8,}(-\d+)?$")


def _is_transient_device_name(name: str) -> bool:
    """Return True iff *name* looks like a transient smoke/test device.

    A name matches if:
    - It exactly equals one of the registered prefixes (e.g. "smoke",
      "vault-smoke"), OR
    - It starts with ``prefix + "-"`` AND the remainder matches a
      hex-stamp pattern (8+ hex chars, optionally followed by ``-N``).

    This prevents short generic prefixes like "hive", "log", "final" from
    nuking real devices named "hive-android-phone" or "final-pc".
    """
    for prefix in _TRANSIENT_DEVICE_PREFIXES:
        if name == prefix:
            return True
        stamp_candidate = name[len(prefix) + 1:]  # slice after "prefix-"
        if name.startswith(prefix + "-") and _TRANSIENT_STAMP_RE.match(stamp_candidate):
            return True
    return False


@router.post("/devices/purge", response_model=PurgeResult)
def purge_devices(
    device=Depends(require_device),
    request: Request = None,
) -> PurgeResult:
    """Drop every currently-revoked device PLUS every transient test
    pairing whose name matches a known smoke/test prefix. Manual
    cleanup hook the user can hit (or the start-gateway script can
    call) to keep the list short.

    Never purges the caller's own device — that would kill the auth
    token mid-request and leave the user unable to confirm what was
    cleaned up.
    """
    st = state(request)
    caller_id = device.id
    n = 0
    # Purge revoked, but only those that aren't the caller (an
    # already-revoked caller wouldn't have authed anyway, so this is
    # belt-and-suspenders).
    for d in list(st.devices.list()):
        if d.revoked and d.id != caller_id:
            if st.devices.purge(d.id):
                n += 1
    # Purge transient prefix-matched devices, again skipping caller.
    for d in list(st.devices.list()):
        if d.id == caller_id:
            continue
        if _is_transient_device_name(d.name):
            if st.devices.purge(d.id):
                n += 1
    return PurgeResult(purged=n)


@router.delete("/devices/{device_id}", status_code=204)
def revoke_device(
    device_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> None:
    """Hard-remove the target device row.

    Authorisation: a device may delete (a) itself, or (b) any device
    NOT marked admin/owner whose audience doesn't strictly dominate
    the caller's. A non-`all` device can no longer purge an `all`-
    audience device — that was a privilege escalation against the
    user's primary phone/PC.

    Audience semantics (matches the rest of the gateway):
      - caller has `all` → can delete any device
      - caller has only `hive` → can delete only `hive` devices
        (or itself)
    """
    st = state(request)
    target = next(
        (d for d in st.devices.list() if d.id == device_id), None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="device not found")

    caller_audiences = set(device.audience or ())
    is_self = device.id == device_id
    if not is_self:
        # Caller must dominate target's audience. 'all' covers everyone.
        target_audiences = set(target.audience or ())
        is_admin = "all" in caller_audiences
        # Allow non-admin only when target's audiences are a subset of
        # caller's — i.e. caller can already see what target can see.
        if not is_admin and not target_audiences.issubset(caller_audiences):
            raise HTTPException(
                status_code=403,
                detail="cannot delete a device with broader audience",
            )
    if not st.devices.purge(device_id):
        raise HTTPException(status_code=404, detail="device not found")
