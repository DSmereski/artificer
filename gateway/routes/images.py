"""Image generation routes.

POST /v1/images            — enqueue a job, return {job_id, state}
POST /v1/render            — Flutter Studio shape (aspect + loras),
                              wraps /v1/images
GET  /v1/images/{job_id}   — poll job state
GET  /v1/media/{id}        — fetch a finished image
"""

from __future__ import annotations

import logging

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from gateway.deps import rate_limited, require_device, require_device_or_loopback, state


router = APIRouter(prefix="/v1", tags=["images"])
log = logging.getLogger("gateway.images")


class ImageRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    count: int = Field(1, ge=1, le=4)
    model: str | None = None
    width: int = Field(1024, ge=64, le=2048)
    height: int = Field(1024, ge=64, le=2048)
    steps: int = Field(20, ge=1, le=80)
    guidance: float = Field(3.5, ge=0.0, le=20.0)
    negative_prompt: str = ""
    seed: int = -1
    enhance: bool = True


class ImageJobInfo(BaseModel):
    id: str
    state: str
    prompt: str
    result_ids: list[str]
    error: str | None = None


@router.post("/images", response_model=ImageJobInfo)
async def enqueue_image(
    body: ImageRequest,
    device=Depends(rate_limited("images")),
    request: Request = None,
) -> ImageJobInfo:
    st = state(request)
    shim = st.image_shim
    if shim is None:
        raise HTTPException(status_code=503, detail="image shim not configured")
    job = await shim.enqueue(
        prompt=body.prompt, count=body.count, model=body.model,
        width=body.width, height=body.height, steps=body.steps,
        guidance=body.guidance, negative_prompt=body.negative_prompt,
        seed=body.seed, enhance=body.enhance,
    )
    return ImageJobInfo(**job.__dict__)


# ---------------------------------------------------------------- /v1/render
# Flutter Studio's render form posts here. The form's payload shape
# differs from /v1/images (aspect string vs explicit width/height,
# `cfg`/`negative` field names, structured `loras` list), so this is a
# thin translation layer rather than a renamed alias.


class _RenderLora(BaseModel):
    # Flutter sends `repo_id`; older callers send `alias`. Accept
    # either — the gateway resolves to alias before handing off to the
    # image shim.
    repo_id: str | None = None
    alias: str | None = None
    strength: float = 1.0


class RenderRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    negative: str = ""
    steps: int = Field(28, ge=1, le=80)
    cfg: float = Field(6.5, ge=0.0, le=20.0)
    seed: int | None = None
    aspect: str = "1:1"
    loras: list[_RenderLora] = Field(default_factory=list)
    model: str | None = None


@router.post("/render")
async def render_image(
    body: RenderRequest,
    device=Depends(rate_limited("images")),
    request: Request = None,
) -> dict:
    from gateway.image_catalog import ASPECT_RATIOS

    st = state(request)
    shim = st.image_shim
    if shim is None:
        raise HTTPException(status_code=503, detail="image shim not configured")

    # The Flutter form uses ratio-style names ('1:1', '16:9', '9:16',
    # '21:9') while ASPECT_RATIOS keys on labels ('square',
    # 'landscape', ...). Accept either by normalising through this
    # alias table before lookup.
    _RATIO_ALIASES = {
        "1:1": "square",
        "4:5": "portrait",
        "9:16": "portrait",
        "3:4": "portrait",
        "16:9": "landscape",
        "4:3": "landscape",
        "21:9": "ultrawide",
        "16:6": "ultrawide",
        "wallpaper": "wallpaper",
    }
    aspect_key = body.aspect.strip().lower()
    aspect_key = _RATIO_ALIASES.get(aspect_key, aspect_key)
    dims = ASPECT_RATIOS.get(aspect_key)
    if dims is None:
        known = sorted(ASPECT_RATIOS.keys()) + sorted(_RATIO_ALIASES.keys())
        raise HTTPException(
            status_code=400,
            detail=f"unknown aspect {body.aspect!r}; known: {known}",
        )
    width, height = dims

    # Resolve LoRA references against the installed registry. The
    # Flutter form sends `repo_id`, the downstream image shim wants
    # `alias`. Drop entries that don't match anything installed so the
    # pipeline doesn't crash on a bad name.
    from gateway.routes.loras import _registry_paths
    import json as _json

    _, registry_path, _ = _registry_paths(request)
    registry: list[dict] = []
    if registry_path.is_file():
        try:
            raw = _json.loads(registry_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                registry = [r for r in raw if isinstance(r, dict)]
        except Exception:  # noqa: BLE001
            registry = []

    def _resolve_alias(spec: _RenderLora) -> str | None:
        if spec.alias:
            return spec.alias
        if spec.repo_id:
            for r in registry:
                if r.get("repo_id") == spec.repo_id:
                    return str(r.get("alias", "")) or None
        return None

    lora_overrides = []
    for spec in body.loras:
        alias = _resolve_alias(spec)
        if not alias:
            log.warning(
                "render: dropping LoRA spec with no resolvable alias: %r",
                spec.model_dump(),
            )
            continue
        lora_overrides.append({"alias": alias, "strength": float(spec.strength)})

    job = await shim.enqueue(
        prompt=body.prompt,
        count=1,
        model=body.model,
        width=width,
        height=height,
        steps=body.steps,
        guidance=body.cfg,
        negative_prompt=body.negative,
        seed=body.seed if body.seed is not None else -1,
        lora_overrides=lora_overrides or None,
    )
    return {"job_id": job.id, "state": job.state}


class RecentImage(BaseModel):
    job_id: str
    bot: str
    prompt: str
    created_at: float
    state: str
    result_ids: list[str]
    error: str | None = None


@router.get("/images/recent", response_model=list[RecentImage])
def recent_images(
    since: float = Query(0.0, ge=0.0),
    bot: str | None = None,
    device=Depends(require_device),
    request: Request = None,
) -> list[RecentImage]:
    """Replay this device's recent image jobs (for app reconnect after sleep).

    `since` is a unix timestamp; jobs created at/after that point are returned.
    Pass `bot=hive` to limit to one bot. State is `running`, `done`, or `error`;
    when `done`, `result_ids[0]` is the media id to fetch via /v1/media/{id}.
    """
    st = state(request)
    store = st.recent_images
    if store is None:
        return []
    jobs = store.recent(device_id=device.id, since_ts=since, bot=bot)
    return [
        RecentImage(
            job_id=j.job_id, bot=j.bot, prompt=j.prompt,
            created_at=j.created_at, state=j.state,
            result_ids=list(j.result_ids), error=j.error,
        )
        for j in jobs
    ]


@router.get("/images/all_recent", response_model=list[RecentImage])
def all_recent_images(
    since: float = Query(0.0, ge=0.0),
    bot: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    device=Depends(require_device),
    request: Request = None,
) -> list[RecentImage]:
    """Cross-device gallery feed. Same shape as /v1/images/recent but
    NOT filtered by `device_id` — every job in the persisted ledger
    is returned, newest first. The Gallery tab uses this so renders
    fired from the phone show up on the PC and vice versa.
    """
    st = state(request)
    store = st.recent_images
    if store is None:
        return []
    jobs = store.all_recent(since_ts=since, bot=bot, limit=limit)
    return [
        RecentImage(
            job_id=j.job_id, bot=j.bot, prompt=j.prompt,
            created_at=j.created_at, state=j.state,
            result_ids=list(j.result_ids), error=j.error,
        )
        for j in jobs
    ]


@router.get("/images/catalog")
def images_catalog(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    """Return the current LoRA / aspect / preset catalog Hive sees."""
    from gateway.image_catalog import ImageCatalog, catalog_as_json
    st = state(request)
    cat = st.image_catalog or ImageCatalog()
    return catalog_as_json(cat)


@router.get("/images/{job_id}", response_model=ImageJobInfo)
def image_job_status(
    job_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> ImageJobInfo:
    st = state(request)
    shim = st.image_shim
    if shim is None:
        raise HTTPException(status_code=503, detail="image shim not configured")
    job = shim.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return ImageJobInfo(**job.__dict__)


class UploadedReference(BaseModel):
    media_id: str
    mime: str
    size_bytes: int


_ALLOWED_UPLOAD_MIMES = {"image/png", "image/jpeg", "image/webp"}
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


@router.post("/images/upload", response_model=UploadedReference)
async def upload_reference_image(
    file: UploadFile = File(...),
    device=Depends(rate_limited("writes")),
    request: Request = None,
) -> UploadedReference:
    """Upload a reference image the user wants to use as a basis for image
    generation (img2img). Returns a media id the client passes back as
    `reference_media_id` in the next chat message; the gateway routes the
    next [GENERATE_IMAGE] turn through img2img.
    """
    if file.content_type not in _ALLOWED_UPLOAD_MIMES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported mime: {file.content_type or '?'}",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (>20 MB)")

    st = state(request)
    uploads_dir = st.config.state_dir / "media-uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    media_id = uuid.uuid4().hex[:12]
    ext = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
    }[file.content_type]
    path = uploads_dir / f"{media_id}{ext}"
    path.write_bytes(data)
    return UploadedReference(
        media_id=media_id, mime=file.content_type, size_bytes=len(data),
    )


# Re-exported from gateway.media_paths so existing route-level callers
# keep working. New code should import from gateway.media_paths directly.
from gateway.media_paths import resolve_uploaded_reference as _resolve_uploaded_reference  # noqa: E402, F401


@router.get("/media/{media_id}")
def fetch_media(
    media_id: str,
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> FileResponse:
    st = state(request)
    shim = st.image_shim
    if shim is None:
        raise HTTPException(status_code=503, detail="image shim not configured")
    # Restrict to hex-like ids to avoid path traversal.
    if not media_id.isalnum() or len(media_id) > 64:
        raise HTTPException(status_code=400, detail="invalid media id")
    path = shim.media_path(media_id)
    if path is None:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path, media_type="image/png")
