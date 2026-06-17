"""Image-gen + events WS tests. ai_generate is faked so no real GPU is touched."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gateway.image_shim import ImageShim


def _install_fake_shim(client: TestClient, tmp_path: Path, monkeypatch) -> ImageShim:
    """Replace the app's image_shim with one whose backend generates placeholders."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    shim = ImageShim(media_dir, on_done=lambda j: None)

    def _fake_invoke(self, params: dict[str, Any]) -> list[str]:
        # Create a deterministic placeholder PNG.
        src = media_dir.parent / f"src-{time.time_ns()}.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return [str(src)]

    monkeypatch.setattr(ImageShim, "_invoke_blocking", _fake_invoke, raising=True)

    # Wire the fake shim into the app state so image_shim callbacks publish events.
    client.app.state.ai_team.image_shim = shim
    return shim


def _wait_done(client: TestClient, token: str, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(
            f"/v1/images/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        info = r.json()
        if info["state"] in ("done", "error"):
            return info
        time.sleep(0.05)
    raise AssertionError("image job never reached terminal state")


def test_image_job_roundtrip(
    client: TestClient, paired_token: tuple[str, str], tmp_path: Path, monkeypatch
) -> None:
    _install_fake_shim(client, tmp_path, monkeypatch)
    _, token = paired_token

    r = client.post(
        "/v1/images",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "a cat", "count": 1, "enhance": False},
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["state"] in {"queued", "running", "done"}

    final = _wait_done(client, token, job["id"])
    assert final["state"] == "done"
    assert final["result_ids"]

    media_id = final["result_ids"][0]
    r = client.get(
        f"/v1/media/{media_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_media_rejects_bad_id(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _, token = paired_token
    # Non-alphanumeric id should be rejected by the route guard.
    r = client.get(
        "/v1/media/not-a-valid-id-with-dashes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400


def test_images_unknown_job(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _, token = paired_token
    r = client.get(
        "/v1/images/nonexistent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404


def test_events_ws_receives_image_done(
    client: TestClient, paired_token: tuple[str, str], tmp_path: Path, monkeypatch
) -> None:
    _install_fake_shim(client, tmp_path, monkeypatch)

    # Swap the image_shim's on_done to publish through the event bus.
    bus = client.app.state.ai_team.event_bus
    def _cb(job):
        bus.publish({
            "type": "image_done",
            "job_id": job.id,
            "state": job.state,
            "result_ids": job.result_ids,
            "error": job.error,
        })
    client.app.state.ai_team.image_shim._on_done = _cb

    _, token = paired_token
    with client.websocket_connect(f"/v1/events?token={token}") as ws:
        job = client.post(
            "/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": "b", "count": 1, "enhance": False},
        ).json()
        # Wait for a terminal-state event.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            event = ws.receive_json()
            if event.get("type") == "image_done" and event.get("job_id") == job["id"]:
                assert event["state"] in ("done", "error")
                return
        raise AssertionError("events WS never delivered image_done")
