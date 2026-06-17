"""POST /v1/stt — transcript-only speech-to-text for the G2 glasses HUD.

Exposes just the ASR stage of the voice pipeline without LLM chat or TTS.
Designed for push-to-talk utterances: short audio only.

Contract (fixed — the G2 app is built against this):
  POST /v1/stt
    Body  : raw audio/wav bytes (16 kHz mono s16le PCM WAV), OR
            multipart form with field `audio` (same bytes).
    Query : ?lang=en  (default "en"; forwarded to ASR if supported — Whisper
            accepts a language hint but the shim currently ignores it; the
            param is accepted so the client contract is stable).
    Auth  : Bearer token in Authorization header (same as all REST routes).
    Limits: body > 2 MB → 400; audio > 30 s duration → 400.

  200 {"text": "<transcript>", "duration_s": <float>}
  400 {"detail": "<reason>"}   (missing / empty / oversize body)
  503 {"detail": "ASR backend unavailable"}
"""

from __future__ import annotations

import logging
import struct
import wave
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from gateway.deps import Device, require_device, state


router = APIRouter(prefix="/v1", tags=["stt"])
log = logging.getLogger("gateway.stt")

_MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_DURATION_S = 30.0              # seconds


def _wav_duration(wav_bytes: bytes) -> float:
    """Return duration in seconds from a WAV header, or raise ValueError."""
    try:
        with wave.open(BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate <= 0:
                raise ValueError("invalid sample rate")
            return frames / rate
    except (wave.Error, EOFError, struct.error) as exc:
        raise ValueError(f"cannot parse WAV header: {exc}") from exc


@router.post("/stt")
async def transcribe(
    request: Request,
    lang: str = Query(default="en"),
    device: Device = Depends(require_device),
) -> dict:
    """Transcript-only STT endpoint.

    Accepts raw WAV bytes as the request body (primary path), or a
    multipart upload with an ``audio`` field (secondary path for clients
    that prefer form encoding).
    """
    content_type = request.headers.get("content-type", "")

    # --- read audio bytes from body or multipart --------------------------
    if "multipart/form-data" in content_type:
        form = await request.form()
        audio_field = form.get("audio")
        if audio_field is None:
            raise HTTPException(status_code=400, detail="multipart field 'audio' missing")
        # SpUploadFile.read() returns bytes; plain str field → reject.
        if isinstance(audio_field, str):
            raise HTTPException(status_code=400, detail="multipart field 'audio' must be a file, not a string")
        wav_bytes = await audio_field.read()
    else:
        wav_bytes = await request.body()

    # --- guard: missing body -----------------------------------------------
    if not wav_bytes:
        raise HTTPException(status_code=400, detail="audio body is empty")

    # --- guard: oversize body ----------------------------------------------
    if len(wav_bytes) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"audio exceeds {_MAX_BODY_BYTES // (1024 * 1024)} MB limit",
        )

    # --- guard: oversize duration (parse WAV header) ----------------------
    try:
        duration_s = _wav_duration(wav_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if duration_s > _MAX_DURATION_S:
        raise HTTPException(
            status_code=400,
            detail=f"audio duration {duration_s:.1f}s exceeds {_MAX_DURATION_S:.0f}s limit",
        )

    # --- ASR ---------------------------------------------------------------
    app_state = state(request)
    pipeline = app_state.voice_pipeline
    if pipeline is None:
        raise HTTPException(status_code=503, detail="ASR backend unavailable")

    import asyncio
    loop = asyncio.get_running_loop()
    try:
        transcript: str = await loop.run_in_executor(
            None,
            lambda: pipeline.transcribe(wav_bytes),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("ASR transcription failed")
        raise HTTPException(status_code=503, detail="ASR backend unavailable") from exc

    return {"text": transcript.strip(), "duration_s": round(duration_s, 3)}
