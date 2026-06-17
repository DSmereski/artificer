"""
Async image generation queue.

Accepts requests from Discord users, processes them one at a time through
the GPU pipeline (discord_generate.py), and delivers completed images
back to the requesting user's channel.
"""

import asyncio
import io
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import discord
from PIL import Image

from shared.delete_button import DeleteButtonView

GENERATE_SCRIPT = Path(r"C:\Projects\imageToVideo\discord_generate.py")

_BAR_LENGTH = 20
_BAR_FILL = "\u2588"   # full block
_BAR_EMPTY = "\u2591"  # light shade


def _build_bar(pct: int) -> str:
    """Build a progress bar string."""
    filled = int(_BAR_LENGTH * pct / 100)
    return _BAR_FILL * filled + _BAR_EMPTY * (_BAR_LENGTH - filled)


def _build_status_generating(
    mention: str, prompt: str, count_label: str,
    pct: int, phase: str, elapsed: float = 0.0,
    current_image: int = 0, total_images: int = 1,
) -> str:
    """Status bar for the job currently generating."""
    bar = _build_bar(pct)
    elapsed_str = f" | {int(elapsed)}s" if elapsed > 0 else ""

    if total_images > 1:
        # Multi-image job: show per-image progress and overall count
        completed = current_image - 1
        overall_pct = int(((completed * 100) + pct) / total_images)
        overall_bar = _build_bar(overall_pct)
        lines = [
            f"{mention} **Generating images** ({completed}/{total_images} done)",
            f"Overall: `[{overall_bar}]` **{overall_pct}%**{elapsed_str}",
            f"Image {current_image}/{total_images}: `[{bar}]` **{pct}%** {phase}",
            f"Prompt: *{prompt}*",
        ]
    else:
        lines = [
            f"{mention} **Generating image**{count_label}",
            f"`[{bar}]` **{pct}%**{elapsed_str}",
            f"{phase}",
            f"Prompt: *{prompt}*",
        ]
    return "\n".join(lines)


def _build_status_queued(
    mention: str, prompt: str, count_label: str, position: int,
) -> str:
    """Status bar for a job waiting in queue."""
    bar = _build_bar(0)
    return (
        f"{mention} **Queued #{position}**{count_label}\n"
        f"`[{bar}]` Waiting...\n"
        f"Prompt: *{prompt}*"
    )
MEDIA_DIR = Path.home() / "Projects" / "Ai-Team" / "media"
LOCK_FILE = Path(r"C:\Projects\imageToVideo\output\.gen_lock")


@dataclass
class ImageRequest:
    prompt: str
    user: discord.User | discord.Member
    channel: discord.abc.Messageable
    message: Optional[discord.Message] = None
    count: int = 1
    model: Optional[str] = None
    width: int = 1024
    height: int = 1024
    steps: int = 20
    guidance: float = 3.5
    negative_prompt: str = ""
    seed: int = -1
    enhance: bool = True
    # LoRAs in the form ai_generate() expects: [{"choice": str, "strength": float}, ...]
    # None = no LoRA overrides; [] = explicitly opt out of auto-LoRA fallback.
    lora_overrides: Optional[list[dict]] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class _TrackedJob:
    """A job in the queue with its Discord status message."""
    request: ImageRequest
    status_msg: Optional[discord.Message] = None
    safe_prompt: str = ""
    count_label: str = ""


class ImageQueue:
    MAX_QUEUE_SIZE = 50

    def __init__(self):
        self._queue: asyncio.Queue[_TrackedJob] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._worker_task: Optional[asyncio.Task] = None
        self._current: Optional[_TrackedJob] = None
        # Ordered list of queued jobs for position tracking
        self._queued_jobs: list[_TrackedJob] = []
        self._lock = asyncio.Lock()

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def is_processing(self) -> bool:
        return self._current is not None

    def position(self) -> int:
        return self._queue.qsize() + (1 if self._current else 0)

    def start(self, loop: asyncio.AbstractEventLoop):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = loop.create_task(self._worker())

    async def enqueue(self, request: ImageRequest) -> int:
        pos = self.position() + 1
        safe_prompt = discord.utils.escape_mentions(request.prompt)[:100]
        count_label = f" ({request.count} images)" if request.count > 1 else ""

        job = _TrackedJob(
            request=request,
            safe_prompt=safe_prompt,
            count_label=count_label,
        )

        # Create status message immediately — shows queued position or generating
        try:
            if pos == 1:
                job.status_msg = await request.channel.send(
                    _build_status_generating(
                        request.user.mention, safe_prompt, count_label,
                        0, "Initializing...",
                    )
                )
            else:
                job.status_msg = await request.channel.send(
                    _build_status_queued(
                        request.user.mention, safe_prompt, count_label, pos - 1,
                    )
                )
        except Exception:
            pass

        async with self._lock:
            self._queued_jobs.append(job)
        await self._queue.put(job)
        return pos

    async def _update_queued_positions(self):
        """Update all queued jobs' status messages with their new positions."""
        async with self._lock:
            jobs = list(self._queued_jobs)
        for i, job in enumerate(jobs):
            if job == self._current:
                continue
            if job.status_msg:
                try:
                    await job.status_msg.edit(
                        content=_build_status_queued(
                            job.request.user.mention, job.safe_prompt,
                            job.count_label, i + 1,
                        )
                    )
                except Exception:
                    pass

    async def _worker(self):
        while True:
            job = await self._queue.get()
            self._current = job

            # Remove from queued list
            async with self._lock:
                if job in self._queued_jobs:
                    self._queued_jobs.remove(job)

            # Update remaining queued jobs' positions
            await self._update_queued_positions()

            try:
                await self._process(job)
            except Exception as e:
                try:
                    print(f"[ImageQueue] Error processing request: {e}", flush=True)
                    await job.request.channel.send(
                        f"{job.request.user.mention} Sorry, image generation failed. Please try again."
                    )
                except Exception:
                    pass
            finally:
                self._current = None
                self._queue.task_done()

    async def _process(self, job: _TrackedJob):
        req = job.request
        status_msg = job.status_msg
        total = req.count
        start_time = time.time()

        # Update status to "generating"
        if status_msg:
            try:
                await status_msg.edit(
                    content=_build_status_generating(
                        req.user.mention, job.safe_prompt, job.count_label,
                        0, "Initializing...",
                        current_image=1, total_images=total,
                    )
                )
            except Exception:
                pass

        all_paths: list[str] = []

        # Generate images one at a time for individual progress tracking
        for img_idx in range(1, total + 1):
            done = asyncio.Event()
            img_start = time.time()

            async def _update_progress(current=img_idx):
                phases = [
                    (0, 5, "Loading model..."),
                    (5, 10, "Encoding prompt..."),
                    (10, 15, "Starting denoising..."),
                    (15, 60, "Denoising..."),
                    (60, 90, "Refining..."),
                    (90, 98, "Finalizing..."),
                ]
                while not done.is_set():
                    await asyncio.sleep(3)
                    if done.is_set():
                        break
                    elapsed_total = time.time() - start_time
                    elapsed_img = time.time() - img_start
                    pct = min(98, int(98 * (1 - 1 / (1 + elapsed_img / 20))))
                    phase_label = "Generating..."
                    for low, high, label in phases:
                        if low <= pct < high:
                            phase_label = label
                            break
                    if pct >= 98:
                        phase_label = "Almost done..."
                    if status_msg:
                        try:
                            await status_msg.edit(
                                content=_build_status_generating(
                                    req.user.mention, job.safe_prompt, job.count_label,
                                    pct, phase_label, elapsed_total,
                                    current_image=current, total_images=total,
                                )
                            )
                        except Exception:
                            pass

            progress_task = asyncio.create_task(_update_progress())

            try:
                loop = asyncio.get_event_loop()
                single_req = ImageRequest(
                    prompt=req.prompt, user=req.user, channel=req.channel,
                    message=req.message, count=1, model=req.model,
                    width=req.width, height=req.height, steps=req.steps,
                    guidance=req.guidance, negative_prompt=req.negative_prompt,
                    seed=req.seed + img_idx - 1 if req.seed >= 0 else -1,
                    enhance=req.enhance,
                )
                paths = await loop.run_in_executor(None, self._generate, single_req)
                all_paths.extend(paths)
            finally:
                done.set()
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

            # Send completed image immediately
            for path in paths:
                p = Path(path)
                if not p.exists():
                    continue
                label = f"Image {img_idx}/{total}" if total > 1 else ""
                caption = f"{req.user.mention} {label}\n**Prompt:** {job.safe_prompt}"
                try:
                    buf, filename = _fit_for_discord(p)
                    requester_id = req.user.id if req.user else None
                    await req.channel.send(
                        content=caption,
                        file=discord.File(buf, filename=filename),
                        view=DeleteButtonView(requester_id),
                    )
                except Exception as e:
                    print(f"[ImageQueue] Failed to send image {img_idx}: {e}", flush=True)

            # Update status bar to show completion of this image
            if status_msg and img_idx < total:
                try:
                    elapsed_total = time.time() - start_time
                    await status_msg.edit(
                        content=_build_status_generating(
                            req.user.mention, job.safe_prompt, job.count_label,
                            0, "Starting next image...", elapsed_total,
                            current_image=img_idx + 1, total_images=total,
                        )
                    )
                except Exception:
                    pass

        elapsed = time.time() - start_time

        if not all_paths:
            if status_msg:
                try:
                    await status_msg.edit(
                        content=f"{req.user.mention} Generation failed. The GPU might be busy or the prompt was rejected."
                    )
                except Exception:
                    pass
            return

        # Show final completion
        if status_msg:
            try:
                await status_msg.edit(
                    content=_build_status_generating(
                        req.user.mention, job.safe_prompt, job.count_label,
                        100, f"All done! ({int(elapsed)}s)", elapsed,
                        current_image=total, total_images=total,
                    )
                )
                await asyncio.sleep(3)
                await status_msg.delete()
            except Exception:
                pass

    @staticmethod
    def _generate(req: ImageRequest) -> list[str]:
        if not _acquire_lock():
            return []

        try:
            sys.path.insert(0, str(GENERATE_SCRIPT.parent))
            from core.ai_generate import ai_generate

            results = ai_generate(
                prompt=req.prompt,
                count=req.count,
                model=req.model,
                width=req.width,
                height=req.height,
                steps=req.steps,
                guidance=req.guidance,
                negative_prompt=req.negative_prompt,
                seed=req.seed,
                enhance=req.enhance,
                lora_overrides=req.lora_overrides,
            )

            return results if results else []

        except Exception as e:
            print(f"[ImageQueue] Generation error: {e}", file=sys.stderr)
            return []
        finally:
            _release_lock()


_DISCORD_MAX_BYTES = 8 * 1024 * 1024  # 8 MB


def _fit_for_discord(path: Path) -> tuple[io.BytesIO, str]:
    file_size = path.stat().st_size

    if file_size <= _DISCORD_MAX_BYTES:
        buf = io.BytesIO(path.read_bytes())
        return buf, path.name

    img = Image.open(path)

    for quality in (92, 80, 65, 50):
        buf = io.BytesIO()
        rgb = img.convert("RGB") if img.mode != "RGB" else img
        rgb.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= _DISCORD_MAX_BYTES:
            buf.seek(0)
            return buf, path.stem + ".jpg"

    for scale in (0.75, 0.5, 0.35):
        new_size = (int(img.width * scale), int(img.height * scale))
        resized = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        rgb = resized.convert("RGB") if resized.mode != "RGB" else resized
        rgb.save(buf, format="JPEG", quality=80, optimize=True)
        if buf.tell() <= _DISCORD_MAX_BYTES:
            buf.seek(0)
            return buf, path.stem + ".jpg"

    buf.seek(0)
    return buf, path.stem + ".jpg"


def _acquire_lock(timeout: int = 600) -> bool:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = LOCK_FILE.open("x")
            fd.write(str(int(time.time())))
            fd.close()
            return True
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
                if age > 600:
                    LOCK_FILE.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(2)
    return False


def _release_lock():
    LOCK_FILE.unlink(missing_ok=True)
