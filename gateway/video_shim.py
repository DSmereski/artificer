"""Discord-free wrapper around imageToVideo's wan_video.generate_wan_video.

Parallel structure to image_shim.ImageShim. Jobs run on a daemon
worker thread with a global lock so video gen serialises against
itself (WAN i2v is GPU-heavy — concurrent runs would crash). The
gateway's main asyncio loop returns immediately from `enqueue()`.

Output .mp4 files land in `<media_dir>/<media_id>.mp4`. The same
event-bus `image_done` channel is reused with `kind: "video"` to
keep the chat-feed plumbing simple.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("gateway.video_shim")

# GPU 0 = RTX 4080 — gaming only. AI workloads must use cuda:1 or cuda:2.
_DEFAULT_VIDEO_DEVICE = os.environ.get("VIDEO_DEVICE", "cuda:1")

_IMAGETOVIDEO = Path(r"C:\Projects\imageToVideo")
if _IMAGETOVIDEO.is_dir() and str(_IMAGETOVIDEO) not in sys.path:
    sys.path.insert(0, str(_IMAGETOVIDEO))


@dataclass
class VideoJob:
    id: str
    state: str = "queued"          # queued | running | done | error
    prompt: str = ""
    result_id: str | None = None   # media_id of finished mp4
    error: str | None = None
    seed_image_path: str = ""
    duration_s: float = 0.0


class VideoShim:
    """Serialised video-gen queue backed by a worker thread."""

    def __init__(
        self,
        media_dir: Path,
        on_done: Callable[[VideoJob], None] | None = None,
    ) -> None:
        self._media_dir = media_dir
        self._media_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, VideoJob] = {}
        self._jobs_lock = threading.Lock()
        # WAN gen consumes both 5060 Ti GPUs; do not run two at once.
        self._worker_lock = threading.Lock()
        self._on_done = on_done

    # ---------------------------------------------------------------- jobs

    def list_jobs(self) -> list[VideoJob]:
        with self._jobs_lock:
            return list(self._jobs.values())

    def get(self, job_id: str) -> VideoJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def media_path(self, media_id: str) -> Path | None:
        target = self._media_dir / f"{media_id}.mp4"
        return target if target.exists() else None

    # ---------------------------------------------------------------- enqueue

    async def enqueue(
        self,
        *,
        prompt: str,
        seed_image_path: str,
        negative_prompt: str = "",
        width: int = 832,
        height: int = 480,
        num_frames: int = 81,
        fps: int = 16,
        seed: int = 0,
        num_steps: int = 20,
        guidance_scale: float = 5.0,
        lora_path: str = "",
        lora_strength: float = 1.0,
        device: str = _DEFAULT_VIDEO_DEVICE,
    ) -> VideoJob:
        if not seed_image_path:
            raise ValueError("seed_image_path is required for WAN i2v")
        job = VideoJob(
            id=uuid.uuid4().hex[:12],
            prompt=prompt,
            seed_image_path=seed_image_path,
        )
        with self._jobs_lock:
            self._jobs[job.id] = job
        params = dict(
            image_path=seed_image_path,
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width, height=height,
            num_frames=num_frames, fps=fps,
            seed=seed, num_steps=num_steps,
            guidance_scale=guidance_scale,
            lora_path=lora_path, lora_strength=lora_strength,
            device=device,
        )
        thread = threading.Thread(
            target=self._run_sync, args=(job, params),
            daemon=True, name=f"video-job-{job.id}",
        )
        thread.start()
        return job

    # ---------------------------------------------------------------- worker

    def _run_sync(self, job: VideoJob, params: dict[str, Any]) -> None:
        try:
            job.state = "running"
            output_path = self._invoke_blocking(params)
            if not output_path:
                job.state = "error"
                job.error = "wan_video produced no output"
                return
            media_id = uuid.uuid4().hex[:12]
            dst = self._media_dir / f"{media_id}.mp4"
            try:
                shutil.copy2(output_path, dst)
            except OSError as e:
                log.warning("video copy failed %s -> %s: %s", output_path, dst, e)
                job.state = "error"
                job.error = f"copy failed: {e}"
                return
            job.result_id = media_id
            job.duration_s = (
                params["num_frames"] / params["fps"]
                if params.get("fps") else 0.0
            )
            job.state = "done"
        except Exception as e:  # noqa: BLE001
            log.exception("video gen failed (job %s)", job.id)
            job.state = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            if self._on_done is not None:
                try:
                    self._on_done(job)
                except Exception:  # noqa: BLE001
                    log.exception("video on_done callback failed")

    def _invoke_blocking(self, params: dict[str, Any]) -> str:
        with self._worker_lock:
            from media.wan_video import generate_wan_video  # type: ignore[import-not-found]
            return generate_wan_video(**params)
