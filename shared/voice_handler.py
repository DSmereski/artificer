"""
Orchestrates the STT -> LLM -> TTS pipeline for a single voice recording.
Runs the CPU-heavy inference in a thread pool to avoid blocking the Discord event loop.

Voice models (Whisper STT, SpeechT5 TTS) are lazy-loaded when joining a voice
channel and unloaded when leaving, so they don't eat GPU VRAM while idle.
"""

import asyncio
import io
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

import discord

from shared.llm_client import LLMClient

# GPU 0 = RTX 4080 (gaming, stay off)
# GPU 1/2 = RTX 5060 Ti (AI workloads)
_VOICE_DEVICE = "cuda:1"


class VoiceHandler:
    def __init__(self):
        self._stt = None
        self._tts = None
        self._llm = LLMClient()
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._model_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Model lifecycle — load on voice join, unload on voice leave
    # ------------------------------------------------------------------

    def load_voice_models(self) -> None:
        """Load STT and TTS models onto GPU. Called when joining voice.
        Thread-safe: concurrent !join commands won't double-load."""
        with self._model_lock:
            if self._stt is None:
                from shared.stt import WhisperSTT
                self._stt = WhisperSTT(device=_VOICE_DEVICE)
            if self._tts is None:
                from shared.tts import SpeechT5TTS
                self._tts = SpeechT5TTS(device=_VOICE_DEVICE)

    def unload_voice_models(self) -> None:
        """Release STT and TTS models from GPU. Called when leaving voice.
        Thread-safe: acquires _model_lock to avoid racing with an
        in-flight handle_utterance call."""
        with self._model_lock:
            if self._stt is not None:
                del self._stt
                self._stt = None
            if self._tts is not None:
                del self._tts
                self._tts = None
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print("[VoiceHandler] Voice models unloaded, VRAM freed.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_utterance(
        self,
        user_id: int,
        wav_bytes: bytes,
        voice_client: discord.VoiceClient,
        channel: discord.TextChannel,
    ) -> None:
        if self._stt is None or self._tts is None:
            return

        loop = asyncio.get_running_loop()

        transcript, reply, speech_bytes = await loop.run_in_executor(
            self._executor,
            self._run_pipeline,
            user_id,
            wav_bytes,
        )

        if not transcript:
            return

        safe_transcript = discord.utils.escape_mentions(transcript)
        safe_reply = discord.utils.escape_mentions(reply)
        await channel.send(f"**You said:** {safe_transcript}\n**Reply:** {safe_reply}")
        await self._play_audio(voice_client, speech_bytes)

    def reset_user(self, user_id: int) -> None:
        self._llm.reset_history(user_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_voice(text: str) -> str:
        text = text.replace("[GENERATE_IMAGE]", "")
        text = re.sub(r"\[/?[A-Z_]{3,}\]", "", text)
        return text.strip()

    def _run_pipeline(
        self, user_id: int, wav_bytes: bytes
    ) -> tuple[str, str, bytes]:
        transcript = self._stt.transcribe(wav_bytes)
        if not transcript:
            return "", "", b""
        transcript = self._sanitize_voice(transcript)
        if not transcript:
            return "", "", b""
        reply = self._llm.chat(user_id, transcript)
        reply = reply.replace("[GENERATE_IMAGE]", "").strip()
        speech_bytes = self._tts.synthesize(reply)
        return transcript, reply, speech_bytes

    @staticmethod
    async def _play_audio(
        voice_client: discord.VoiceClient, wav_bytes: bytes
    ) -> None:
        if not wav_bytes:
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name

        try:
            event = asyncio.Event()

            def after(_error):
                event.set()

            source = discord.FFmpegPCMAudio(tmp_path)
            voice_client.play(source, after=after)
            await event.wait()
        finally:
            os.unlink(tmp_path)
