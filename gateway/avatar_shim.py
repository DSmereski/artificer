"""Talking-head avatar shim: script -> kokoro TTS (wav) -> SadTalker (mp4).

Parallel structure to image_shim / video_shim. A job runs on a daemon worker
thread; a global lock serialises renders (SadTalker is VRAM-heavy — concurrent
runs would thrash the 5060 Tis). Two local HTTP services do the work:

  kokoro-TTS   POST {kokoro_url}/v1/audio/speech   (OpenAI-compatible) -> wav
  SadTalker    POST {sadtalker_url}/generate        image + audio      -> mp4

Both services are pinned to the 5060 Tis (CUDA_VISIBLE_DEVICES=1,2) at the
container level — see scripts/start_content_services.ps1. The RTX 4080 (GPU 0)
stays gaming-only. Output .mp4 lands in <media_dir>/<media_id>.mp4 so it shares
the same media gallery as image/video content.

Service URLs + timeouts are env-configurable (HIVE_KOKORO_URL,
HIVE_SADTALKER_URL, HIVE_KOKORO_TIMEOUT, HIVE_SADTALKER_TIMEOUT).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

log = logging.getLogger("gateway.avatar_shim")

_KOKORO_URL = os.environ.get("HIVE_KOKORO_URL", "http://127.0.0.1:8880").rstrip("/")
_SADTALKER_URL = os.environ.get("HIVE_SADTALKER_URL", "http://127.0.0.1:8085").rstrip("/")
_KOKORO_TIMEOUT = float(os.environ.get("HIVE_KOKORO_TIMEOUT", "180"))
_SADTALKER_TIMEOUT = float(os.environ.get("HIVE_SADTALKER_TIMEOUT", "600"))
_DEFAULT_VOICE = os.environ.get("HIVE_KOKORO_VOICE", "af_heart")
_VALID_PREPROCESS = {"crop", "resize", "full"}


@dataclass
class AvatarJob:
    id: str
    state: str = "queued"          # queued | running | done | error
    script: str = ""
    result_ids: list[str] = field(default_factory=list)
    error: str | None = None


class AvatarShim:
    """Serialised talking-head queue: kokoro audio -> SadTalker video."""

    def __init__(
        self,
        media_dir: Path,
        on_done: Callable[[AvatarJob], None] | None = None,
        *,
        kokoro_url: str = _KOKORO_URL,
        sadtalker_url: str = _SADTALKER_URL,
        http_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._media_dir = media_dir
        self._media_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, AvatarJob] = {}
        self._jobs_lock = threading.Lock()
        self._worker_lock = threading.Lock()   # one render at a time (VRAM)
        self._on_done = on_done
        self._kokoro_url = kokoro_url.rstrip("/")
        self._sadtalker_url = sadtalker_url.rstrip("/")
        # Injectable so tests can supply a fake HTTP client (no live service).
        self._http_factory = http_factory or (lambda: httpx.Client())

    # ---------------------------------------------------------------- jobs

    def list_jobs(self) -> list[AvatarJob]:
        with self._jobs_lock:
            return list(self._jobs.values())

    def get(self, job_id: str) -> AvatarJob | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def media_path(self, media_id: str) -> Path | None:
        target = self._media_dir / f"{media_id}.mp4"
        return target if target.exists() else None

    # ---------------------------------------------------------------- enqueue

    async def enqueue(
        self,
        *,
        script: str,
        image_path: str | None = None,
        avatar_name: str = "ai_woman",
        voice: str = _DEFAULT_VOICE,
        preprocess: str = "crop",
        still: bool = False,
    ) -> AvatarJob:
        """Enqueue a talking-head render. `script` is spoken by kokoro; the
        resulting audio drives SadTalker over `image_path` (a face image) or a
        predefined `avatar_name`. Runs on a worker thread; returns immediately.
        """
        script = (script or "").strip()
        if not script:
            raise ValueError("avatar request needs a script")
        if preprocess not in _VALID_PREPROCESS:
            raise ValueError(
                f"invalid preprocess {preprocess!r}; must be one of {sorted(_VALID_PREPROCESS)}"
            )
        job = AvatarJob(id=uuid.uuid4().hex[:12], script=script)
        with self._jobs_lock:
            self._jobs[job.id] = job
        params = dict(
            script=script, image_path=image_path, avatar_name=avatar_name,
            voice=voice, preprocess=preprocess, still=bool(still),
        )
        thread = threading.Thread(
            target=self._run_sync, args=(job, params),
            daemon=True, name=f"avatar-job-{job.id}",
        )
        thread.start()
        return job

    # ---------------------------------------------------------------- worker

    def _run_sync(self, job: AvatarJob, params: dict[str, Any]) -> None:
        try:
            job.state = "running"
            with self._worker_lock:   # serialise — SadTalker is VRAM-heavy
                wav_path = self._synthesize_audio(job.id, params["script"], params["voice"])
                mp4_src = self._render_video(wav_path, params)
            media_id = uuid.uuid4().hex[:12]
            dst = self._media_dir / f"{media_id}.mp4"
            shutil.copy2(mp4_src, dst)
            job.result_ids = [media_id]
            job.state = "done"
        except Exception as e:  # noqa: BLE001
            log.exception("avatar gen failed (job %s)", job.id)
            job.state = "error"
            job.error = f"{type(e).__name__}: {e}"
        finally:
            if self._on_done is not None:
                try:
                    self._on_done(job)
                except Exception:  # noqa: BLE001
                    log.exception("on_done callback failed")

    # -- kokoro: text -> wav -------------------------------------------------

    def _synthesize_audio(self, job_id: str, script: str, voice: str) -> Path:
        out = self._media_dir / f"{job_id}.wav"
        with self._http_factory() as client:
            resp = client.post(
                f"{self._kokoro_url}/v1/audio/speech",
                json={
                    "model": "kokoro", "input": script,
                    "voice": voice, "response_format": "wav",
                },
                timeout=_KOKORO_TIMEOUT,
            )
            resp.raise_for_status()
            out.write_bytes(resp.content)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("kokoro produced empty audio")
        return out

    # -- sadtalker: image + wav -> mp4 ---------------------------------------
    #
    # MULTIPART UPLOAD with return_file=true: SadTalker runs in a container that
    # cannot see the gateway's host filesystem, so we upload the face image + wav
    # bytes and get the rendered .mp4 back in the response body. This avoids any
    # shared-volume / path-translation coupling (verified live 2026-06-21).

    def _render_video(self, wav_path: Path, params: dict[str, Any]) -> str:
        image_path = params.get("image_path")
        if not image_path or not os.path.exists(str(image_path)):
            raise RuntimeError("avatar render needs a face image (image_media_id)")
        out = self._media_dir / f"{uuid.uuid4().hex[:12]}.sad.mp4"
        with self._http_factory() as client:
            with open(str(image_path), "rb") as imgf, open(wav_path, "rb") as audf:
                resp = client.post(
                    f"{self._sadtalker_url}/generate",
                    files={
                        "source_image": ("face.png", imgf, "image/png"),
                        "audio": ("audio.wav", audf, "audio/wav"),
                    },
                    data={
                        "preprocess": params["preprocess"],
                        "still": str(bool(params["still"])).lower(),
                        "return_file": "true",
                    },
                    timeout=_SADTALKER_TIMEOUT,
                )
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                data = resp.json()
                raise RuntimeError(f"sadtalker: {data.get('error', 'no video returned')}")
            out.write_bytes(resp.content)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("sadtalker produced empty video")
        return str(out)
