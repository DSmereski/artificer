"""POST /v1/stt — transcript-only STT endpoint tests.

Monkeypatches the VoicePipeline.transcribe() method exactly like
test_voice.py patches the full pipeline, so no Whisper model loads.
"""

from __future__ import annotations

import io
import wave

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wav(duration_s: float = 0.1, sample_rate: int = 16000) -> bytes:
    """Build a valid minimal WAV (mono s16le) of the requested duration."""
    n_frames = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


def _install_fake_pipeline(client: TestClient, transcript: str = "hello world") -> None:
    """Replace voice_pipeline with a stub that returns a fixed transcript."""

    class _FakePipeline:
        def transcribe(self, wav_bytes: bytes) -> str:  # noqa: ARG002
            return transcript

        # run_pipeline must exist so voice route still works during the test run
        def run_pipeline(self, *, wav_bytes, llm_reply, user_id):  # noqa: ARG002
            raise NotImplementedError("not used in STT tests")

    client.app.state.ai_team.voice_pipeline = _FakePipeline()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_stt_happy_path_raw_body(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """Raw audio/wav body returns 200 with transcript and duration."""
    _, token = paired_token
    _install_fake_pipeline(client)

    wav = _make_wav(duration_s=0.5)
    resp = client.post(
        "/v1/stt",
        content=wav,
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["text"] == "hello world"
    assert isinstance(data["duration_s"], float)
    assert 0.4 < data["duration_s"] < 0.6


def test_stt_happy_path_multipart(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """Multipart audio field is accepted and returns the same shape."""
    _, token = paired_token
    _install_fake_pipeline(client, transcript="hey there")

    wav = _make_wav(duration_s=0.2)
    resp = client.post(
        "/v1/stt",
        files={"audio": ("clip.wav", wav, "audio/wav")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["text"] == "hey there"
    assert isinstance(data["duration_s"], float)


def test_stt_missing_body_returns_400(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """Empty body → 400."""
    _, token = paired_token
    _install_fake_pipeline(client)

    resp = client.post(
        "/v1/stt",
        content=b"",
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_stt_oversize_body_returns_400(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """Body > 2 MB → 400 before touching the ASR backend."""
    _, token = paired_token
    _install_fake_pipeline(client)

    big_body = b"\x00" * (2 * 1024 * 1024 + 1)
    resp = client.post(
        "/v1/stt",
        content=big_body,
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "2 mb" in detail or "exceed" in detail or "limit" in detail


def test_stt_oversize_duration_returns_400(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """WAV longer than 30 s → 400 without running ASR."""
    _, token = paired_token
    _install_fake_pipeline(client)

    wav = _make_wav(duration_s=31.0)
    resp = client.post(
        "/v1/stt",
        content=wav,
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "30" in detail or "duration" in detail or "exceed" in detail


def test_stt_requires_auth(client: TestClient) -> None:
    """No Bearer token → 401."""
    _install_fake_pipeline(client)

    wav = _make_wav(duration_s=0.1)
    resp = client.post(
        "/v1/stt",
        content=wav,
        headers={"Content-Type": "audio/wav"},
    )
    assert resp.status_code == 401


def test_stt_asr_unavailable_returns_503(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """voice_pipeline=None (ASR unavailable) → 503."""
    _, token = paired_token
    client.app.state.ai_team.voice_pipeline = None

    wav = _make_wav(duration_s=0.1)
    resp = client.post(
        "/v1/stt",
        content=wav,
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


def test_stt_invalid_wav_returns_400(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    """Non-WAV bytes that are small enough but unparseable → 400."""
    _, token = paired_token
    _install_fake_pipeline(client)

    resp = client.post(
        "/v1/stt",
        content=b"not a wav file at all",
        headers={
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer {token}",
        },
    )
    assert resp.status_code == 400
    assert "wav" in resp.json()["detail"].lower() or "parse" in resp.json()["detail"].lower()
