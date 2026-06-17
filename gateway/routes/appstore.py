"""Operator app store — Android app-store routes.

GET  /v1/appstore             — JSON catalog list. Open (no auth), loopback/tailnet only.
GET  /v1/appstore/{id}        — single catalog entry, 404 if absent.
GET  /v1/appstore/{id}/apk    — download the APK from disk (FileResponse).
POST /v1/appstore/{id}/upload — publish/replace an APK + upsert the catalog entry.
                                Gated: a valid device Bearer token OR a loopback
                                caller (the publish skill runs on the same PC).

The catalog JSON and the APK files live under the gateway's persistent state/
dir so they survive a restart. Each entry's ``apkUrl`` is baked with the
configured tailnet base URL so the phone fetches over the tailnet, not loopback.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from gateway.config import Config
from gateway.deps import require_device_or_loopback
from shared.atomic_write import atomic_write_bytes, atomic_write_json

router = APIRouter(prefix="/v1/appstore", tags=["appstore"])
log = logging.getLogger("gateway.appstore")

# App ids become a filename segment — keep them to a safe slug so they can
# never escape the apk dir. Validated before any path join.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")

# Fields the Flutter AppListing.fromJson requires. Catalog entries must carry
# all of them or the client throws on parse.
_REQUIRED_FIELDS = (
    "id", "name", "packageId", "icon", "description",
    "category", "version", "apkUrl", "screenshots", "updatedAt",
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_config(request: Request) -> Config:
    return request.app.state.ai_team.config


def _apk_dir(config: Config) -> Path:
    return Path(config.appstore_apk_dir)


def _catalog_path(config: Config) -> Path:
    return Path(config.appstore_catalog_path)


def _icon_dir(config: Config) -> Path:
    # Sibling of the apk dir under the persistent state/appstore dir.
    return Path(config.appstore_apk_dir).parent / "icons"


def _backdrop_dir(config: Config) -> Path:
    return Path(config.appstore_apk_dir).parent / "backdrops"


def _public_base(config: Config) -> str:
    return str(config.appstore_public_base_url).rstrip("/")


def _validate_id(app_id: str) -> str:
    if not _ID_RE.match(app_id):
        raise HTTPException(status_code=404, detail="app not found")
    return app_id


def _load_catalog(config: Config) -> list[dict[str, Any]]:
    """Return the catalog as a list of entry dicts. Empty list if absent."""
    path = _catalog_path(config)
    if not path.is_file():
        return []
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("appstore: catalog read failed (%s) — treating as empty", exc)
        return []
    if not isinstance(data, list):
        log.warning("appstore: catalog is not a list — treating as empty")
        return []
    return [e for e in data if isinstance(e, dict)]


def _save_catalog(config: Config, entries: list[dict[str, Any]]) -> None:
    atomic_write_json(_catalog_path(config), entries, indent=2)


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("")
def list_apps(request: Request) -> Any:
    """Return all catalog entries, newest update first. Open/read-only."""
    config = _get_config(request)
    entries = _load_catalog(config)
    entries.sort(key=lambda e: str(e.get("updatedAt", "")), reverse=True)
    return entries


@router.get("/{app_id}")
def get_app(app_id: str, request: Request) -> Any:
    """Return a single catalog entry, or 404."""
    config = _get_config(request)
    _validate_id(app_id)
    for entry in _load_catalog(config):
        if entry.get("id") == app_id:
            return entry
    raise HTTPException(status_code=404, detail="app not found")


@router.get("/{app_id}/apk")
def download_apk(app_id: str, request: Request) -> FileResponse:
    """Serve the APK for the given id from disk.

    Path-traversal guard: the resolved path must sit inside the apk dir.
    """
    config = _get_config(request)
    _validate_id(app_id)

    apk_dir = _apk_dir(config).resolve()
    apk_path = (apk_dir / f"{app_id}.apk").resolve()

    try:
        apk_path.relative_to(apk_dir)
    except ValueError:
        log.warning("appstore: path traversal attempt — id=%s path=%s", app_id, apk_path)
        raise HTTPException(status_code=404, detail="app not found")

    if not apk_path.is_file():
        raise HTTPException(status_code=404, detail="apk not found on disk")

    return FileResponse(
        str(apk_path),
        media_type="application/vnd.android.package-archive",
        filename=f"{app_id}.apk",
    )


@router.get("/{app_id}/icon")
def download_icon(app_id: str, request: Request) -> FileResponse:
    """Serve the app's thumbnail (PNG) from disk. 404 if none set."""
    config = _get_config(request)
    _validate_id(app_id)

    icon_dir = _icon_dir(config).resolve()
    icon_path = (icon_dir / f"{app_id}.png").resolve()
    try:
        icon_path.relative_to(icon_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="icon not found")
    if not icon_path.is_file():
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(str(icon_path), media_type="image/png")


@router.post("/{app_id}/icon")
async def upload_icon(
    app_id: str,
    request: Request,
    icon: UploadFile = File(...),
    _auth=Depends(require_device_or_loopback),
) -> Any:
    """Set the app's thumbnail from a captured launch-screen PNG.

    Stores the image and points the catalog entry's ``icon`` at the served
    URL. The app must already exist in the catalog (publish it first).
    """
    config = _get_config(request)
    _validate_id(app_id)

    body = await icon.read()
    # PNG ("\x89PNG") or JPEG ("\xff\xd8") magic only.
    is_png = body[:4] == b"\x89PNG"
    is_jpg = body[:2] == b"\xff\xd8"
    if not (is_png or is_jpg):
        raise HTTPException(status_code=400, detail="icon must be a PNG or JPEG")

    icon_path = _icon_dir(config) / f"{app_id}.png"
    atomic_write_bytes(icon_path, body)

    entries = _load_catalog(config)
    found = False
    icon_url = f"{_public_base(config)}/v1/appstore/{app_id}/icon"
    for entry in entries:
        if entry.get("id") == app_id:
            entry["icon"] = icon_url
            entry["updatedAt"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="app not in catalog — publish it first")
    _save_catalog(config, entries)
    log.info("appstore: set icon for %s (%d bytes)", app_id, len(body))
    return {"ok": True, "id": app_id, "icon": icon_url, "sizeBytes": len(body)}


@router.get("/{app_id}/backdrop")
def download_backdrop(app_id: str, request: Request) -> FileResponse:
    """Serve the app's backdrop (launch-screen PNG). 404 if none."""
    config = _get_config(request)
    _validate_id(app_id)
    bdir = _backdrop_dir(config).resolve()
    path = (bdir / f"{app_id}.png").resolve()
    try:
        path.relative_to(bdir)
    except ValueError:
        raise HTTPException(status_code=404, detail="backdrop not found")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="backdrop not found")
    return FileResponse(str(path), media_type="image/png")


@router.post("/{app_id}/backdrop")
async def upload_backdrop(
    app_id: str,
    request: Request,
    backdrop: UploadFile = File(...),
    _auth=Depends(require_device_or_loopback),
) -> Any:
    """Set the app's backdrop (the captured launch screen). Stored separately
    from the launcher icon; pointed at by the catalog entry's first screenshot."""
    config = _get_config(request)
    _validate_id(app_id)

    body = await backdrop.read()
    if not (body[:4] == b"\x89PNG" or body[:2] == b"\xff\xd8"):
        raise HTTPException(status_code=400, detail="backdrop must be a PNG or JPEG")

    atomic_write_bytes(_backdrop_dir(config) / f"{app_id}.png", body)

    entries = _load_catalog(config)
    url = f"{_public_base(config)}/v1/appstore/{app_id}/backdrop"
    found = False
    for entry in entries:
        if entry.get("id") == app_id:
            entry["screenshots"] = [url]
            entry["updatedAt"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="app not in catalog — publish it first")
    _save_catalog(config, entries)
    log.info("appstore: set backdrop for %s (%d bytes)", app_id, len(body))
    return {"ok": True, "id": app_id, "backdrop": url, "sizeBytes": len(body)}


@router.post("/{app_id}/upload")
async def upload_apk(
    app_id: str,
    request: Request,
    apk: UploadFile = File(...),
    name: str = Form(...),
    packageId: str = Form(...),
    version: str = Form(...),
    description: str = Form(""),
    category: str = Form("Apps"),
    icon: str = Form(""),
    notes: str = Form(""),
    _auth=Depends(require_device_or_loopback),
) -> Any:
    """Publish (or replace) an APK and upsert its catalog entry.

    Gated by ``require_device_or_loopback``: the publish skill runs on the same
    machine as the gateway (loopback passes), and tailnet callers still need a
    device token. The phone never hits this route — it only GETs.
    """
    config = _get_config(request)
    _validate_id(app_id)

    body = await apk.read()
    # Reject anything that isn't a ZIP (APKs are zip containers — "PK\x03\x04").
    if len(body) < 4 or body[:2] != b"PK":
        raise HTTPException(status_code=400, detail="uploaded file is not an APK (no PK magic)")

    apk_path = _apk_dir(config) / f"{app_id}.apk"
    atomic_write_bytes(apk_path, body)

    entry = {
        "id": app_id,
        "name": name,
        "packageId": packageId,
        "icon": icon,
        "description": description,
        "category": category,
        "version": version,
        "apkUrl": f"{_public_base(config)}/v1/appstore/{app_id}/apk",
        "screenshots": [],
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
        "sizeBytes": len(body),
    }

    entries = _load_catalog(config)
    # Preserve existing screenshots/icon if the new upload left them blank.
    for existing in entries:
        if existing.get("id") == app_id:
            if not entry["icon"]:
                entry["icon"] = existing.get("icon", "")
            if not entry["screenshots"]:
                entry["screenshots"] = existing.get("screenshots", [])
            break
    entries = [e for e in entries if e.get("id") != app_id]
    entries.append(entry)
    _save_catalog(config, entries)

    log.info("appstore: published %s v%s (%d bytes)", app_id, version, len(body))
    return entry
