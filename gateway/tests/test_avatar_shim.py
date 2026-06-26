"""Unit tests for the talking-head avatar shim (kokoro -> SadTalker).

No live services: a fake httpx client is injected via `http_factory`. The
SadTalker call is a MULTIPART upload that returns the rendered .mp4 in the
response body (return_file=true). Covers the happy path, input validation,
service failure, and the single-render serialisation guard.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from gateway.avatar_shim import AvatarShim


def _face(tmp_path: Path) -> str:
    p = tmp_path / "face.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return str(p)


class _FakeResp:
    def __init__(self, *, content: bytes = b"", json_data=None,
                 headers=None, raise_exc=None):
        self.content = content
        self._json = json_data or {}
        self.headers = headers or {}
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, **kw):
        return self._handler(url, kw)


def _wait(job, timeout: float = 10.0):
    end = time.time() + timeout
    while time.time() < end:
        if job.state in ("done", "error"):
            return job
        time.sleep(0.02)
    return job


def _ok_handler():
    """kokoro -> wav bytes; sadtalker -> mp4 bytes (video/mp4)."""
    def handler(url, kw):
        if url.endswith("/v1/audio/speech"):
            return _FakeResp(content=b"RIFF....WAVEfmt ")
        if url.endswith("/generate"):
            assert "files" in kw and "source_image" in kw["files"] and "audio" in kw["files"]
            return _FakeResp(content=b"\x00\x00\x00\x18ftypmp42",
                             headers={"content-type": "video/mp4"})
        raise AssertionError(f"unexpected url {url}")
    return handler


def test_enqueue_renders_mp4(tmp_path):
    media = tmp_path / "media"
    shim = AvatarShim(media, http_factory=lambda: _FakeClient(_ok_handler()))
    job = asyncio.run(shim.enqueue(script="Hello world", image_path=_face(tmp_path)))
    job = _wait(job)
    assert job.state == "done", job.error
    assert len(job.result_ids) == 1
    out = shim.media_path(job.result_ids[0])
    assert out is not None and out.exists() and out.stat().st_size > 0


def test_missing_face_image_rejected(tmp_path):
    shim = AvatarShim(tmp_path / "media", http_factory=lambda: _FakeClient(_ok_handler()))
    job = asyncio.run(shim.enqueue(script="hi", image_path=None))
    job = _wait(job)
    assert job.state == "error"
    assert "face image" in (job.error or "")


def test_empty_script_rejected(tmp_path):
    shim = AvatarShim(tmp_path / "media", http_factory=lambda: _FakeClient(_ok_handler()))
    with pytest.raises(ValueError):
        asyncio.run(shim.enqueue(script="   "))


def test_invalid_preprocess_rejected(tmp_path):
    shim = AvatarShim(tmp_path / "media", http_factory=lambda: _FakeClient(_ok_handler()))
    with pytest.raises(ValueError):
        asyncio.run(shim.enqueue(script="hi", preprocess="bogus"))


def test_kokoro_failure_marks_error(tmp_path):
    def handler(url, kw):
        if url.endswith("/v1/audio/speech"):
            return _FakeResp(raise_exc=RuntimeError("kokoro down"))
        raise AssertionError("should not reach sadtalker")
    shim = AvatarShim(tmp_path / "media", http_factory=lambda: _FakeClient(handler))
    job = asyncio.run(shim.enqueue(script="hi", image_path=_face(tmp_path)))
    job = _wait(job)
    assert job.state == "error"
    assert "kokoro down" in (job.error or "")


def test_sadtalker_json_error_marks_error(tmp_path):
    def handler(url, kw):
        if url.endswith("/v1/audio/speech"):
            return _FakeResp(content=b"wav")
        return _FakeResp(json_data={"error": "bad face"},
                         headers={"content-type": "application/json"})
    shim = AvatarShim(tmp_path / "media", http_factory=lambda: _FakeClient(handler))
    job = asyncio.run(shim.enqueue(script="hi", image_path=_face(tmp_path)))
    job = _wait(job)
    assert job.state == "error"
    assert "bad face" in (job.error or "")


def test_single_render_serialisation(tmp_path):
    """The worker lock must guarantee at most one render in flight."""
    concurrent = {"now": 0, "max": 0}
    lock = threading.Lock()

    def handler(url, kw):
        if url.endswith("/v1/audio/speech"):
            return _FakeResp(content=b"wav")
        with lock:
            concurrent["now"] += 1
            concurrent["max"] = max(concurrent["max"], concurrent["now"])
        time.sleep(0.1)
        with lock:
            concurrent["now"] -= 1
        return _FakeResp(content=b"mp4bytes", headers={"content-type": "video/mp4"})

    face = _face(tmp_path)
    shim = AvatarShim(tmp_path / "media", http_factory=lambda: _FakeClient(handler))
    j1 = asyncio.run(shim.enqueue(script="one", image_path=face))
    j2 = asyncio.run(shim.enqueue(script="two", image_path=face))
    _wait(j1)
    _wait(j2)
    assert j1.state == "done" and j2.state == "done"
    assert concurrent["max"] == 1, f"renders overlapped (peak {concurrent['max']})"
