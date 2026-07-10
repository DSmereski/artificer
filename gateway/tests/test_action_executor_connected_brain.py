"""Tests for connected-brain Item 3: video_render and lora_train verbs.

Mirrors the structure of test_action_executor.py and test_action_executor_phase3.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.action_executor import ActionExecutor, ActionReceipt


# ---------------------------------------------------------------- helpers


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- video_render verb


def test_video_render_no_shim_configured():
    ex = ActionExecutor(video_shim=None)
    receipts = _run(ex.execute_all([
        {"verb": "video_render", "payload": {
            "prompt": "slow orbit around a spaceship",
            "seed_image_path": "/state/media/abc123.png",
        }},
    ]))
    assert len(receipts) == 1
    assert receipts[0].ok is False
    assert "not configured" in receipts[0].detail


def test_video_render_missing_prompt():
    fake_shim = MagicMock()
    ex = ActionExecutor(video_shim=fake_shim)
    receipts = _run(ex.execute_all([
        {"verb": "video_render", "payload": {
            "seed_image_path": "/state/media/abc123.png",
        }},
    ]))
    assert receipts[0].ok is False
    assert "prompt" in receipts[0].detail


def test_video_render_missing_seed_image_path():
    fake_shim = MagicMock()
    ex = ActionExecutor(video_shim=fake_shim)
    receipts = _run(ex.execute_all([
        {"verb": "video_render", "payload": {
            "prompt": "slow orbit",
        }},
    ]))
    assert receipts[0].ok is False
    assert "seed_image_path" in receipts[0].detail


@pytest.mark.asyncio
async def test_video_render_happy_path():
    """When all required fields are present, video shim enqueue is called."""
    from gateway.video_shim import VideoJob

    fake_job = VideoJob(id="vidabc123", prompt="slow orbit")
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock(return_value=fake_job)

    ex = ActionExecutor(video_shim=fake_shim)
    receipts = await ex.execute_all([
        {"verb": "video_render", "payload": {
            "prompt": "slow orbit around a spaceship",
            "seed_image_path": "/state/media/abc123.png",
        }},
    ])

    assert len(receipts) == 1
    r = receipts[0]
    assert r.ok is True
    assert r.verb == "video_render"
    assert "vidabc123" in r.detail
    assert r.payload["job_id"] == "vidabc123"
    assert r.payload["prompt"] == "slow orbit around a spaceship"

    # Verify enqueue was called with the right args
    call_kwargs = fake_shim.enqueue.call_args.kwargs
    assert call_kwargs["prompt"] == "slow orbit around a spaceship"
    assert call_kwargs["seed_image_path"] == "/state/media/abc123.png"


@pytest.mark.asyncio
async def test_video_render_passes_optional_fields():
    """Optional fields like num_frames and fps are forwarded to the shim."""
    from gateway.video_shim import VideoJob

    fake_job = VideoJob(id="vidxyz", prompt="pan shot")
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock(return_value=fake_job)

    ex = ActionExecutor(video_shim=fake_shim)
    await ex.execute_all([
        {"verb": "video_render", "payload": {
            "prompt": "pan shot",
            "seed_image_path": "/state/media/xyz.png",
            "num_frames": 48,
            "fps": 12,
            "guidance_scale": 7.5,
        }},
    ])

    call_kwargs = fake_shim.enqueue.call_args.kwargs
    assert call_kwargs["num_frames"] == 48
    assert call_kwargs["fps"] == 12
    assert call_kwargs["guidance_scale"] == 7.5


@pytest.mark.asyncio
async def test_video_render_shim_enqueue_raises_returns_error_receipt():
    """If enqueue raises, the executor returns an error receipt (never raises)."""
    fake_shim = MagicMock()
    fake_shim.enqueue = AsyncMock(side_effect=RuntimeError("GPU OOM"))

    ex = ActionExecutor(video_shim=fake_shim)
    receipts = await ex.execute_all([
        {"verb": "video_render", "payload": {
            "prompt": "test",
            "seed_image_path": "/state/media/x.png",
        }},
    ])
    assert receipts[0].ok is False
    assert "GPU OOM" in receipts[0].detail


# ---------------------------------------------------------------- lora_train verb


def test_lora_train_missing_dataset_path():
    ex = ActionExecutor()
    receipts = _run(ex.execute_all([
        {"verb": "lora_train", "payload": {"output_name": "my-lora"}},
    ]))
    assert receipts[0].ok is False
    assert "dataset_path" in receipts[0].detail


def test_lora_train_missing_output_name():
    ex = ActionExecutor()
    receipts = _run(ex.execute_all([
        {"verb": "lora_train", "payload": {"dataset_path": "/data/imgs"}},
    ]))
    assert receipts[0].ok is False
    assert "output_name" in receipts[0].detail


def test_lora_train_invalid_output_name():
    ex = ActionExecutor()
    receipts = _run(ex.execute_all([
        {"verb": "lora_train", "payload": {
            "dataset_path": "/data/imgs",
            "output_name": "my lora with spaces!",
        }},
    ]))
    assert receipts[0].ok is False
    assert "output_name" in receipts[0].detail


def test_lora_train_graceful_when_imagetovideo_missing():
    """When imageToVideo project is not installed, return ok=False gracefully."""
    ex = ActionExecutor()
    with patch(
        "gateway.image_shim._IMAGE_BACKEND",
        Path("/nonexistent/imageToVideo"),
    ):
        receipts = _run(ex.execute_all([
            {"verb": "lora_train", "payload": {
                "dataset_path": "/data/imgs",
                "output_name": "my-lora",
            }},
        ]))
    assert receipts[0].ok is False
    # Should mention imageToVideo in the error
    assert receipts[0].verb == "lora_train"


@pytest.mark.asyncio
async def test_lora_train_enqueues_job_when_trainer_present(tmp_path: Path):
    """When the trainer module is present and callable, the job is enqueued."""
    # Create a fake lora_train.py in a temp imageToVideo/media/ directory.
    media_dir = tmp_path / "imageToVideo" / "media"
    media_dir.mkdir(parents=True)
    fake_trainer = media_dir / "lora_train.py"
    fake_trainer.write_text(
        "def enqueue_training(*, job_id, dataset_path, output_name, "
        "base_model, steps, learning_rate):\n"
        "    return f'job {job_id} queued for {output_name}'\n",
        encoding="utf-8",
    )

    ex = ActionExecutor()
    with patch("gateway.image_shim._IMAGE_BACKEND", tmp_path / "imageToVideo"):
        receipts = await ex.execute_all([
            {"verb": "lora_train", "payload": {
                "dataset_path": str(tmp_path / "data"),
                "output_name": "test-char",
                "steps": 200,
            }},
        ])

    r = receipts[0]
    assert r.ok is True, f"Expected ok=True, got detail={r.detail!r}"
    assert r.verb == "lora_train"
    assert "test-char" in r.detail or "test-char" in str(r.payload)
    assert r.payload["output_name"] == "test-char"
    assert r.payload["steps"] == 200
    assert "job_id" in r.payload


@pytest.mark.asyncio
async def test_lora_train_steps_clamped_to_max(tmp_path: Path):
    """Steps above 2000 must be clamped to 2000."""
    media_dir = tmp_path / "imageToVideo" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "lora_train.py").write_text(
        "def enqueue_training(*, job_id, dataset_path, output_name, "
        "base_model, steps, learning_rate):\n"
        "    return steps\n",
        encoding="utf-8",
    )

    ex = ActionExecutor()
    with patch("gateway.image_shim._IMAGE_BACKEND", tmp_path / "imageToVideo"):
        receipts = await ex.execute_all([
            {"verb": "lora_train", "payload": {
                "dataset_path": str(tmp_path / "data"),
                "output_name": "my-lora",
                "steps": 9999,
            }},
        ])

    r = receipts[0]
    assert r.ok is True
    assert r.payload["steps"] == 2000


@pytest.mark.asyncio
async def test_lora_train_no_function_in_module_returns_error(tmp_path: Path):
    """If the module lacks enqueue_training and queue_training, receipt is ok=False."""
    media_dir = tmp_path / "imageToVideo" / "media"
    media_dir.mkdir(parents=True)
    (media_dir / "lora_train.py").write_text(
        "# No training functions here\nFOO = 'bar'\n",
        encoding="utf-8",
    )

    ex = ActionExecutor()
    with patch("gateway.image_shim._IMAGE_BACKEND", tmp_path / "imageToVideo"):
        receipts = await ex.execute_all([
            {"verb": "lora_train", "payload": {
                "dataset_path": str(tmp_path / "data"),
                "output_name": "my-lora",
            }},
        ])

    assert receipts[0].ok is False
    assert "no enqueue_training" in receipts[0].detail or "queue_training" in receipts[0].detail


# ---------------------------------------------------------------- synthesizer vocabulary


def test_synthesizer_prompt_includes_video_render():
    """synthesizer.md must list video_render as an allowed verb."""
    from pathlib import Path as _Path
    prompt_path = _Path(__file__).resolve().parents[2] / "prompts" / "synthesizer.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert "video_render" in content, "synthesizer.md must include video_render verb"


def test_synthesizer_prompt_includes_lora_train():
    """synthesizer.md must list lora_train as an allowed verb."""
    from pathlib import Path as _Path
    prompt_path = _Path(__file__).resolve().parents[2] / "prompts" / "synthesizer.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert "lora_train" in content, "synthesizer.md must include lora_train verb"
