"""Voice pipeline adapter.

Wraps the existing WhisperSTT + SpeechT5TTS so the gateway can call them
without pulling in Discord's voice-channel machinery. Models load lazily
on first use; callers can release them with ``VoicePipeline.unload()``.

The pipeline is blocking (STT/TTS run on GPU); callers use
``asyncio.to_thread`` or an executor.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


log = logging.getLogger("gateway.voice_shim")


_DEFAULT_DEVICE = os.environ.get("VOICE_DEVICE", "cuda:1")


@dataclass
class PipelineResult:
    transcript: str
    reply_text: str
    reply_wav: bytes


class VoicePipeline:
    """Lazy-loaded Whisper + SpeechT5 pipeline. Thread-safe for single-worker use."""

    def __init__(self, device: str = _DEFAULT_DEVICE) -> None:
        self._device = device
        self._stt: Any = None
        self._tts: Any = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._stt is None:
                from shared.stt import WhisperSTT
                log.info("loading Whisper STT on %s", self._device)
                self._stt = WhisperSTT(device=self._device)
            if self._tts is None:
                from shared.tts import SpeechT5TTS
                log.info("loading SpeechT5 TTS on %s", self._device)
                self._tts = SpeechT5TTS(device=self._device)

    def unload(self) -> None:
        """Free GPU memory. Next call will reload."""
        with self._lock:
            self._stt = None
            self._tts = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass

    def transcribe(self, wav_bytes: bytes) -> str:
        self._ensure_loaded()
        return self._stt.transcribe(wav_bytes)

    def synthesize(self, text: str) -> bytes:
        self._ensure_loaded()
        return self._tts.synthesize(text)

    def run_pipeline(
        self,
        *,
        wav_bytes: bytes,
        llm_reply: Any,
        user_id: int,
    ) -> PipelineResult:
        """STT -> LLM -> TTS. ``llm_reply`` is a callable (user_id, text) -> str.

        Split out so the gateway can wire any bot adapter's reply function
        (Maggy/Hive/Scout) without the pipeline knowing about adapters.
        """
        transcript = self.transcribe(wav_bytes).strip()
        if not transcript:
            return PipelineResult(transcript="", reply_text="", reply_wav=b"")
        reply_text = llm_reply(user_id, transcript).strip()
        reply_wav = self.synthesize(reply_text) if reply_text else b""
        return PipelineResult(
            transcript=transcript, reply_text=reply_text, reply_wav=reply_wav,
        )
