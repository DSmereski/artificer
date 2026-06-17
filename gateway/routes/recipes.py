"""/v1/recipes — saved Civitai image recipes (positive + negative + sampler/steps/cfg).

User pastes the URL+prompt block into the Models tab, gateway parses
it via `asset_importer._run_image_recipe`, and persists a vault note.
This module surfaces those notes through REST so the app can list,
test (re-render), and delete them.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from gateway.deps import rate_limited, require_device, state


router = APIRouter(prefix="/v1/recipes", tags=["recipes"])
log = logging.getLogger("gateway.recipes_route")


def _check_images_bucket(st, device_id: str) -> bool:
    """Try to consume one token from the images rate bucket.

    Returns True on success, False when the bucket is empty. Used by
    the auto_generate seed-image path which fires two GPU jobs (a
    still then a WAN video) — the route-level Depends only charges
    once, so this gives us a second deliberate charge.
    """
    rl = st.rate_limiter
    if rl is None or not device_id:
        return True
    return rl.try_acquire(device_id, "images")


def _store(request: Request):
    st = state(request)
    s = st.recipe_store
    if s is None:
        raise HTTPException(503, "recipe store not initialised")
    return s


# ---------------------------------------------------------------- list / get


@router.get("")
def list_recipes(
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    return {"recipes": [r.to_json() for r in s.list()]}


@router.get("/{image_id}")
def get_recipe(
    image_id: int,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    r = s.get(image_id)
    if r is None:
        raise HTTPException(404, f"recipe not found: {image_id}")
    return r.to_json()


# ---------------------------------------------------------------- test (render)


class TestRecipeRequest(BaseModel):
    """Optional body for POST /v1/recipes/{id}/test.

    For still recipes the body is ignored.
    For video recipes:
      - seed_mode: "last_rendered" | "auto_generate" | "uploaded"
      - seed_media_id: required if seed_mode == "uploaded"
    """
    seed_mode: str = Field("last_rendered")
    seed_media_id: str | None = None


@router.post("/{image_id}/test")
async def test_recipe(
    image_id: int,
    body: TestRecipeRequest | None = None,
    device=Depends(rate_limited("images")),
    request: Request = None,
) -> dict:
    """Render the saved recipe.

    - kind=still → image_shim.enqueue (text-to-image)
    - kind=video → seed image lookup + video_shim.enqueue (WAN i2v)

    Returns a uniform `{kind, job_id, state, error}` shape so the app
    knows which feed (image_done vs video_done) to listen on.
    """
    s = _store(request)
    r = s.get(image_id)
    if r is None:
        raise HTTPException(404, f"recipe not found: {image_id}")
    if not r.positive:
        raise HTTPException(
            400,
            "recipe has no positive prompt — re-import with the prompt block",
        )

    st = state(request)
    body = body or TestRecipeRequest()

    if r.kind == "video":
        return await _test_video_recipe(r, body, st, device, request)
    return await _test_still_recipe(r, st)


async def _test_still_recipe(r, st) -> dict:
    shim = st.image_shim
    if shim is None:
        raise HTTPException(503, "image shim not configured")
    job = await shim.enqueue(
        prompt=r.positive,
        count=1, model=None,
        width=1024, height=1024,
        steps=int(r.steps) if r.steps else 20,
        guidance=float(r.cfg) if r.cfg else 3.5,
        negative_prompt=r.negative or "",
        seed=int(r.seed) if r.seed is not None else -1,
        enhance=True,
    )
    # Remember the rendered still as the device's last seed image.
    # We mark it now even though the image isn't done — by the time
    # the user taps Test on a video recipe, this will have populated.
    return {
        "kind": "still",
        "job_id": job.id,
        "state": job.state,
        "prompt": job.prompt,
        "result_ids": list(job.result_ids),
        "error": job.error,
    }


async def _test_video_recipe(r, body: TestRecipeRequest, st, device, request: Request) -> dict:
    """Three seed-image modes:
       last_rendered  → most recent done job in RecentImagesStore for this device
       auto_generate  → fire image_shim with the recipe positive prompt,
                        wait, then chain into video_shim
       uploaded       → use the supplied seed_media_id (uploaded reference)
    """
    video_shim = st.video_shim
    image_shim = st.image_shim
    if video_shim is None:
        raise HTTPException(503, "video shim not configured")
    if image_shim is None:
        raise HTTPException(503, "image shim not configured")

    mode = body.seed_mode
    seed_path: str

    if mode == "uploaded":
        if not body.seed_media_id:
            raise HTTPException(400, "seed_media_id required for uploaded mode")
        from gateway.routes.images import _resolve_uploaded_reference
        p = _resolve_uploaded_reference(st.config.state_dir, body.seed_media_id)
        if p is None:
            raise HTTPException(404, "uploaded seed image not found")
        seed_path = str(p)
    elif mode == "last_rendered":
        # Walk the RecentImagesStore for this device and grab the
        # most recent done job. Same store the chat feed uses, so
        # anything the user rendered through any path is eligible.
        media_id: str | None = None
        recent = st.recent_images
        if recent is not None:
            for j in recent.recent(device_id=device.id):
                if j.state == "done" and j.result_ids:
                    media_id = j.result_ids[0]
                    break
        if not media_id:
            raise HTTPException(
                400,
                "no last-rendered image for this device — generate a still first, "
                "or use seed_mode=auto_generate / uploaded",
            )
        p = image_shim.media_path(media_id)
        if p is None:
            raise HTTPException(
                410, "last-rendered image is gone (cleared from disk)",
            )
        seed_path = str(p)
    elif mode == "auto_generate":
        # Two GPU jobs (still + WAN video) get charged against the
        # `images` bucket separately. The route-level dependency
        # consumed one token; charge a second one here so the bucket
        # accounting stays honest. (H-3 from the audit.)
        if not _check_images_bucket(st, device.id):
            raise HTTPException(
                429, "image rate limit exceeded; auto_generate needs 2 tokens",
            )
        # Synchronously fire image_shim, await the worker thread to finish,
        # then use the result as the seed. We poll the job state since
        # the underlying shim is thread-based, not async.
        import asyncio
        still_job = await image_shim.enqueue(
            prompt=r.positive, count=1,
            width=832, height=480,    # WAN's native 16:9 ratio
            steps=int(r.steps) if r.steps else 20,
            guidance=float(r.cfg) if r.cfg else 3.5,
            negative_prompt=r.negative or "",
            seed=int(r.seed) if r.seed is not None else -1,
            enhance=False,
        )
        # Poll for ≤120 s; if the still doesn't finish in time, error out.
        seed_path: str | None = None
        for _ in range(240):
            await asyncio.sleep(0.5)
            current = image_shim.get(still_job.id)
            if current is None:
                continue
            if current.state == "done" and current.result_ids:
                rendered = image_shim.media_path(current.result_ids[0])
                if rendered is None:
                    raise HTTPException(
                        502,
                        "auto-generated still completed but its file "
                        "is gone — check disk / state_dir",
                    )
                seed_path = str(rendered)
                break
            if current.state == "error":
                raise HTTPException(
                    502,
                    f"auto-generated still failed: {current.error}",
                )
        if seed_path is None:
            raise HTTPException(504, "auto-generated still timed out (>120 s)")
    else:
        raise HTTPException(400, f"unknown seed_mode: {mode!r}")

    job = await video_shim.enqueue(
        prompt=r.positive,
        seed_image_path=seed_path,
        negative_prompt=r.negative or "",
        seed=int(r.seed) if r.seed is not None else 0,
        num_steps=int(r.steps) if r.steps else 20,
        guidance_scale=float(r.cfg) if r.cfg else 5.0,
    )
    return {
        "kind": "video",
        "job_id": job.id,
        "state": job.state,
        "prompt": job.prompt,
        "result_id": job.result_id,
        "duration_s": job.duration_s,
        "error": job.error,
    }


# ---------------------------------------------------------------- delete


@router.delete("/{image_id}")
def delete_recipe(
    image_id: int,
    device=Depends(require_device),
    request: Request = None,
) -> dict:
    s = _store(request)
    if not s.delete(image_id):
        raise HTTPException(404, f"recipe not found: {image_id}")
    return {"deleted": image_id}
