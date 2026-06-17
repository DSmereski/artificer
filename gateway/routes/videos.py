"""Video generation routes.

POST /v1/videos              — enqueue a WAN i2v job, return {job_id, state}
GET  /v1/videos/{job_id}     — poll job state
GET  /v1/media/{id}.mp4      — fetch a finished video (handled by /v1/media)

Video gen is **i2v only** for now — every job needs a starting image.
The recipe-test path (POST /v1/recipes/{id}/test) handles seed-image
selection and ultimately calls into here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from gateway.deps import rate_limited, require_device, require_device_or_loopback, state


router = APIRouter(prefix="/v1", tags=["videos"])
log = logging.getLogger("gateway.videos")


class VideoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    seed_image_media_id: str = Field(..., min_length=1, max_length=64)
    negative_prompt: str = ""
    width: int = Field(832, ge=64, le=1920)
    height: int = Field(480, ge=64, le=1920)
    num_frames: int = Field(81, ge=17, le=241)
    fps: int = Field(16, ge=8, le=30)
    seed: int = 0
    num_steps: int = Field(20, ge=4, le=80)
    guidance_scale: float = Field(5.0, ge=0.0, le=20.0)


class VideoJobInfo(BaseModel):
    id: str
    state: str
    prompt: str
    result_id: str | None = None
    duration_s: float = 0.0
    error: str | None = None


def _shim(request: Request):
    st = state(request)
    s = st.video_shim
    if s is None:
        raise HTTPException(503, "video shim not configured")
    return s


def _resolve_seed_image_path(request: Request, media_id: str) -> str:
    """Find the real on-disk path of a media_id (uploaded ref OR rendered still)."""
    from gateway.routes.images import _resolve_uploaded_reference
    st = state(request)
    # Try rendered still first (image_shim emits PNGs).
    img = st.image_shim.media_path(media_id) if st.image_shim else None
    if img is not None:
        return str(img)
    # Then uploaded reference.
    ref = _resolve_uploaded_reference(st.config.state_dir, media_id)
    if ref is not None:
        return str(ref)
    raise HTTPException(
        404, f"seed image not found: {media_id} (must be a rendered still or uploaded reference)",
    )


@router.post("/videos", response_model=VideoJobInfo)
async def enqueue_video(
    body: VideoRequest,
    device=Depends(rate_limited("images")),    # share the image rate bucket
    request: Request = None,
) -> VideoJobInfo:
    shim = _shim(request)
    seed_path = _resolve_seed_image_path(request, body.seed_image_media_id)
    job = await shim.enqueue(
        prompt=body.prompt,
        seed_image_path=seed_path,
        negative_prompt=body.negative_prompt,
        width=body.width, height=body.height,
        num_frames=body.num_frames, fps=body.fps,
        seed=body.seed, num_steps=body.num_steps,
        guidance_scale=body.guidance_scale,
    )
    return VideoJobInfo(
        id=job.id, state=job.state, prompt=job.prompt,
        result_id=job.result_id, duration_s=job.duration_s,
        error=job.error,
    )


@router.get("/videos/recent", response_model=list[VideoJobInfo])
def recent_videos(
    limit: int = 20,
    device=Depends(require_device),
    request: Request = None,
) -> list[VideoJobInfo]:
    """Return the most recent video jobs (across all devices).

    Used by the app's Activity tab. The shim keeps an in-memory job
    list — this returns up to `limit` jobs sorted by job id (which
    embeds creation order) with most recent first.
    """
    shim = _shim(request)
    jobs = shim.list_jobs()
    # Sort by created_at if the shim tracks it, else by id descending
    # (shim ids are monotonic random suffixes, so id-desc ≈ recent-first
    # within a session). Fall back gracefully.
    jobs_sorted = sorted(
        jobs,
        key=lambda j: getattr(j, "created_at", 0.0),
        reverse=True,
    )
    return [
        VideoJobInfo(
            id=j.id, state=j.state, prompt=j.prompt,
            result_id=j.result_id, duration_s=j.duration_s,
            error=j.error,
        )
        for j in jobs_sorted[:max(1, min(limit, 100))]
    ]


@router.get("/videos/{job_id}", response_model=VideoJobInfo)
def video_job_status(
    job_id: str,
    device=Depends(require_device),
    request: Request = None,
) -> VideoJobInfo:
    shim = _shim(request)
    job = shim.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown video job")
    return VideoJobInfo(
        id=job.id, state=job.state, prompt=job.prompt,
        result_id=job.result_id, duration_s=job.duration_s,
        error=job.error,
    )


@router.get("/media/video/{media_id}")
def fetch_video(
    media_id: str,
    device=Depends(require_device_or_loopback),
    request: Request = None,
) -> FileResponse:
    shim = _shim(request)
    if not media_id.isalnum() or len(media_id) > 64:
        raise HTTPException(status_code=400, detail="invalid media id")
    path = shim.media_path(media_id)
    if path is None:
        raise HTTPException(status_code=404, detail="video not found")
    return FileResponse(path, media_type="video/mp4")
