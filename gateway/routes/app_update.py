"""Self-hosted Android APK distribution.

Endpoints:
  GET  /v1/app/version          — APK identity (size, mtime, filename)
  POST /v1/app/download_ticket  — issue a 60s HMAC-signed download URL
  GET  /v1/app/latest.apk       — download via header bearer OR signed ticket

The Flutter app polls /version on startup. When the gateway has a newer
APK on disk, the app POSTs to /download_ticket (auth'd via header) and
gets back a one-time URL with `t=<expiry>&sig=<hmac>`. The app then
launches that URL in the system browser. Browsers can't set
`Authorization: Bearer` headers from a URL launch, so the signed ticket
is the in-band proof of authorisation. Tickets:
  - expire in 60s
  - are scoped to a single APK mtime (so they can't be re-used for a
    later APK)
  - are signed with HMAC-SHA256 over `device_id + apk_mtime + expiry`
    using the gateway's pairing-secret HMAC key
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from gateway.deps import require_device, state


router = APIRouter(prefix="/v1/app", tags=["app-update"])
log = logging.getLogger("gateway.app_update")


def _apk_path_candidates() -> list[Path]:
    """Return candidate APK paths from env vars or empty list if unset."""
    candidates: list[Path] = []
    env_path = os.environ.get("HIVE_APK_PATH", "")
    if env_path:
        candidates.append(Path(env_path))
    return candidates
_TICKET_TTL_S = 60


def _find_apk() -> Path | None:
    for p in _apk_path_candidates():
        if p.is_file():
            return p
    return None


def _ticket_secret(state_dir: Path) -> bytes:
    """Stable per-installation HMAC secret. Stored alongside the device
    store so it survives gateway restarts but doesn't leak into git."""
    secret_path = state_dir / "apk_ticket.secret"
    if not secret_path.is_file():
        import secrets
        secret_path.write_bytes(secrets.token_bytes(32))
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass    # Windows doesn't enforce
    return secret_path.read_bytes()


def _sign(secret: bytes, device_id: str, apk_mtime: int, expiry: int) -> str:
    msg = f"{device_id}|{apk_mtime}|{expiry}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


class AppVersion(BaseModel):
    available: bool
    version_id: int               # int(mtime); compare with last-seen on the client
    size_bytes: int
    filename: str


class DownloadTicket(BaseModel):
    url: str                      # full /v1/app/latest.apk?... URL
    expires_at: int               # unix seconds
    apk_mtime: int


@router.get("/version", response_model=AppVersion)
def app_version(
    device=Depends(require_device),
    request: Request = None,
) -> AppVersion:
    """Return the latest APK's identity. The version_id is the file's mtime
    as an integer; clients should remember the last-installed value and
    prompt when a higher one appears."""
    apk = _find_apk()
    if apk is None:
        return AppVersion(
            available=False, version_id=0, size_bytes=0,
            filename="",
        )
    stat = apk.stat()
    return AppVersion(
        available=True,
        version_id=int(stat.st_mtime),
        size_bytes=stat.st_size,
        filename=apk.name,
    )


@router.post("/download_ticket", response_model=DownloadTicket)
def download_ticket(
    device=Depends(require_device),
    request: Request = None,
) -> DownloadTicket:
    """Issue a short-lived signed URL for the in-app updater.

    Replaces the prior `?token=<bearer>` flow which leaked the bearer
    token into browser history / Tailscale flow logs. This URL is
    valid for 60s and only for THIS apk_mtime (a fresh build
    invalidates outstanding tickets)."""
    apk = _find_apk()
    if apk is None:
        raise HTTPException(status_code=404, detail="apk not built yet")
    apk_mtime = int(apk.stat().st_mtime)
    expiry = int(time.time()) + _TICKET_TTL_S
    st = state(request)
    secret = _ticket_secret(st.config.state_dir)
    sig = _sign(secret, device.id, apk_mtime, expiry)
    base = str(request.base_url).rstrip("/")
    url = (
        f"{base}/v1/app/latest.apk?"
        f"d={device.id}&m={apk_mtime}&e={expiry}&sig={sig}"
    )
    return DownloadTicket(url=url, expires_at=expiry, apk_mtime=apk_mtime)


_bearer_optional = HTTPBearer(auto_error=False)


def _device_from_header_or_ticket(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
    d: str | None = Query(default=None),
    m: int | None = Query(default=None),
    e: int | None = Query(default=None),
    sig: str | None = Query(default=None),
):
    """Auth: bearer header OR a fresh signed ticket (`?d=...&m=...&e=...&sig=...`).

    Bearer header is the API/programmatic path. The signed ticket is
    used by the phone's in-app updater after launching the URL into
    the system browser (which can't carry custom headers).
    """
    st = state(request)

    # 1. Header bearer (normal API path).
    if credentials and credentials.credentials:
        device = st.devices.verify(credentials.credentials)
        if device is None:
            raise HTTPException(status_code=401, detail="invalid token")
        st.devices.touch(device.id)
        return device

    # 2. Signed ticket.
    if d and m is not None and e is not None and sig:
        if int(time.time()) > e:
            raise HTTPException(status_code=401, detail="ticket expired")
        apk = _find_apk()
        if apk is None:
            raise HTTPException(status_code=404, detail="apk not built yet")
        # Ticket must be scoped to the CURRENT apk_mtime — a stale
        # ticket from before today's rebuild is not honoured.
        if int(apk.stat().st_mtime) != m:
            raise HTTPException(status_code=401, detail="ticket stale")
        secret = _ticket_secret(st.config.state_dir)
        expected = _sign(secret, d, m, e)
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(status_code=401, detail="invalid signature")
        # Find the device by id (must exist).
        for dev in st.devices.list():
            if dev.id == d:
                st.devices.touch(dev.id)
                return dev
        raise HTTPException(status_code=401, detail="unknown device")

    raise HTTPException(status_code=401, detail="missing bearer or ticket")


@router.api_route("/latest.apk", methods=["GET", "HEAD"])
def latest_apk(
    device=Depends(_device_from_header_or_ticket),
    request: Request = None,
) -> FileResponse:
    apk = _find_apk()
    if apk is None:
        raise HTTPException(status_code=404, detail="apk not built yet")
    return FileResponse(
        apk,
        media_type="application/vnd.android.package-archive",
        filename="ai-team-app-latest.apk",
        headers={
            # Tell the client to download not stream-render.
            "Content-Disposition": 'attachment; filename="ai-team-app-latest.apk"',
        },
    )
