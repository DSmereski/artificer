"""Discord-free wrapper around the image-generation backend's core.ai_generate.

Jobs run on a dedicated daemon thread so the request's asyncio loop can
return immediately. The backend's own file lock is re-used so any other caller
+ the gateway serialise against each other.

The backend lives wherever ``HIVE_IMAGE_BACKEND_PATH`` points (a checkout that
exposes ``core.ai_generate``); if unset, the image routes stay disabled.
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

log = logging.getLogger("gateway.image_shim")

_IMAGE_BACKEND = Path(os.environ.get("HIVE_IMAGE_BACKEND_PATH", ""))
if str(_IMAGE_BACKEND) not in ("", ".") and _IMAGE_BACKEND.is_dir() and str(_IMAGE_BACKEND) not in sys.path:
    sys.path.insert(0, str(_IMAGE_BACKEND))


@dataclass
class ImageJob:
    id: str
    state: str = "queued"          # queued | running | done | error
    prompt: str = ""
    result_ids: list[str] = field(default_factory=list)
    error: str | None = None


class ImageShim:
    """Serialised image-gen queue backed by a worker thread."""

    def __init__(self, media_dir: Path, on_done: Callable[[ImageJob], None] | None = None) -> None:
        self._media_dir = media_dir
        self._media_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, ImageJob] = {}
        self._jobs_lock = threading.Lock()
        self._worker_lock = threading.Lock()   # serialises actual generation calls
        self._on_done = on_done

    # ---------------------------------------------------------------- jobs

    def list_jobs(self) -> list[ImageJob]:
        with self._jobs_lock:
            return list(self._jobs.values())

    def get(self, job_id: str) -> ImageJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def media_path(self, media_id: str) -> Path | None:
        target = self._media_dir / f"{media_id}.png"
        return target if target.exists() else None

    # ---------------------------------------------------------------- enqueue

    async def enqueue(
        self,
        *,
        prompt: str,
        count: int = 1,
        model: str | None = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        guidance: float = 3.5,
        negative_prompt: str = "",
        seed: int = -1,
        enhance: bool = True,
        lora_overrides: list[dict] | None = None,
        reference_path: str | None = None,
        strength: float = 0.6,
    ) -> ImageJob:
        """Enqueue an image job.

        When `reference_path` is given, runs an img2img turn (variation of
        the supplied source image) with `strength` controlling how far the
        result drifts from the source (0.0 = identical, 1.0 = ignore source).
        Without a reference, uses the standard text-to-image path.
        """
        job = ImageJob(id=uuid.uuid4().hex[:12], prompt=prompt)
        with self._jobs_lock:
            self._jobs[job.id] = job

        if reference_path:
            params = dict(
                prompt=prompt, source_image=reference_path,
                strength=float(strength), num_takes=count,
                model_choice=model or "FLUX (FHDR) - Built-in",
                steps=steps, guidance=guidance, true_cfg=0.0,
                negative_prompt=negative_prompt, seed=seed,
                multi_lora=lora_overrides,
            )
            target = self._run_img2img
        else:
            params = dict(
                prompt=prompt, count=count, model=model,
                width=width, height=height, steps=steps, guidance=guidance,
                negative_prompt=negative_prompt, seed=seed,
                lora_overrides=lora_overrides, enhance=enhance,
            )
            target = self._run_sync

        thread = threading.Thread(
            target=target, args=(job, params),
            daemon=True, name=f"image-job-{job.id}",
        )
        thread.start()
        return job

    # ---------------------------------------------------------------- worker

    def _run_sync(self, job: ImageJob, params: dict[str, Any]) -> None:
        try:
            job.state = "running"
            result_paths = self._invoke_blocking(params)
            media_ids: list[str] = []
            for src in result_paths:
                media_id = uuid.uuid4().hex[:12]
                dst = self._media_dir / f"{media_id}.png"
                try:
                    shutil.copy2(src, dst)
                    media_ids.append(media_id)
                except OSError as e:
                    log.warning("copy failed %s -> %s: %s", src, dst, e)
            job.result_ids = media_ids
            job.state = "done" if media_ids else "error"
            if not media_ids:
                job.error = "no output files produced"
        except Exception as e:  # noqa: BLE001
            log.exception("image gen failed (job %s)", job.id)
            job.state = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            if self._on_done is not None:
                try:
                    self._on_done(job)
                except Exception:  # noqa: BLE001
                    log.exception("on_done callback failed")

    def _invoke_blocking(self, params: dict[str, Any]) -> list[str]:
        with self._worker_lock:
            from core.ai_generate import ai_generate  # type: ignore[import-not-found]
            result = ai_generate(**params)
            if not isinstance(result, list):
                return []
            return [str(p) for p in result]

    def _run_img2img(self, job: ImageJob, params: dict[str, Any]) -> None:
        """Variation of `_run_sync` that calls img2img instead of text-to-image."""
        try:
            job.state = "running"
            result_paths = self._invoke_img2img(params)
            media_ids: list[str] = []
            for src in result_paths:
                media_id = uuid.uuid4().hex[:12]
                dst = self._media_dir / f"{media_id}.png"
                try:
                    shutil.copy2(src, dst)
                    media_ids.append(media_id)
                except OSError as e:
                    log.warning("copy failed %s -> %s: %s", src, dst, e)
            job.result_ids = media_ids
            job.state = "done" if media_ids else "error"
            if not media_ids:
                job.error = "no output files produced"
        except Exception as e:  # noqa: BLE001
            log.exception("img2img failed (job %s)", job.id)
            job.state = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            if self._on_done is not None:
                try:
                    self._on_done(job)
                except Exception:  # noqa: BLE001
                    log.exception("on_done callback failed")

    def _invoke_img2img(self, params: dict[str, Any]) -> list[str]:
        with self._worker_lock:
            from core.generation import generate_img2img_fn  # type: ignore[import-not-found]
            result = generate_img2img_fn(**params)
            # generate_img2img_fn returns either list[str] or (list[str], list[str])
            if isinstance(result, tuple) and result:
                paths = result[0]
            else:
                paths = result
            if not isinstance(paths, list):
                return []
            return [str(p) for p in paths]
