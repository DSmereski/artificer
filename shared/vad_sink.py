"""
Real-time Voice Activity Detection sink for discord.py.

Converts Discord's 48 kHz stereo PCM to 16 kHz mono, runs WebRTC VAD on
each 20 ms frame, and fires an async callback with WAV bytes whenever a
complete utterance (speech followed by silence) is detected.
"""

import asyncio
import audioop
import collections
import io
import wave

import discord
import webrtcvad

# Audio constants
_RATE_IN = 48_000          # Discord delivers 48 kHz stereo 16-bit PCM
_RATE_VAD = 16_000         # WebRTC VAD accepts 8 / 16 / 32 kHz
_FRAME_MS = 20             # VAD frame duration (10 / 20 / 30 ms)
_FRAME_BYTES_VAD = int(_RATE_VAD * _FRAME_MS / 1000) * 2  # 640 bytes

# Tuning
_VAD_AGGRESSIVENESS = 2    # 0 (least) - 3 (most aggressive filtering)
_SILENCE_FRAMES = 35       # ~700 ms of silence -> end of utterance
_MIN_SPEECH_FRAMES = 8     # ignore utterances shorter than ~160 ms
_PREROLL_FRAMES = 5        # frames kept before speech onset (~100 ms)
_MAX_SPEECH_FRAMES = 1500  # ~30s cap — prevents unbounded buffer growth


class VADSink(discord.sinks.Sink):
    """
    Drop-in replacement for WaveSink that processes audio continuously.

    `callback` is an async coroutine called with (user_id: int, wav_bytes: bytes)
    whenever a complete utterance is detected.
    """

    def __init__(self, callback, loop: asyncio.AbstractEventLoop):
        super().__init__(filters=None)
        self._vad = webrtcvad.Vad(_VAD_AGGRESSIVENESS)
        self._callback = callback
        self._loop = loop
        self._state: dict[int, dict] = {}

    # ------------------------------------------------------------------
    # AudioSink interface
    # ------------------------------------------------------------------

    def write(self, data: bytes, user) -> None:
        if user is None:
            return
        uid = user.id
        s = self._state.setdefault(uid, {
            "resample_state": None,
            "ring": collections.deque(maxlen=_PREROLL_FRAMES),
            "speech": [],
            "silence_count": 0,
            "speaking": False,
        })

        # --- Resample: 48 kHz stereo -> 16 kHz mono ---
        mono = audioop.tomono(data, 2, 0.5, 0.5)
        resampled, s["resample_state"] = audioop.ratecv(
            mono, 2, 1, _RATE_IN, _RATE_VAD, s["resample_state"]
        )

        # --- Process in VAD-sized frames ---
        offset = 0
        while offset + _FRAME_BYTES_VAD <= len(resampled):
            frame = resampled[offset: offset + _FRAME_BYTES_VAD]
            offset += _FRAME_BYTES_VAD
            self._process_frame(uid, s, frame)

    def cleanup(self) -> None:
        self._state.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_frame(self, uid: int, s: dict, frame: bytes) -> None:
        try:
            is_speech = self._vad.is_speech(frame, _RATE_VAD)
        except Exception:
            return

        if is_speech:
            if not s["speaking"]:
                s["speech"] = list(s["ring"])
                s["speaking"] = True
                s["silence_count"] = 0
            s["speech"].append(frame)
            if len(s["speech"]) >= _MAX_SPEECH_FRAMES:
                self._flush(uid, s)
        else:
            s["ring"].append(frame)
            if s["speaking"]:
                s["silence_count"] += 1
                s["speech"].append(frame)

                if s["silence_count"] >= _SILENCE_FRAMES:
                    self._flush(uid, s)

    def _flush(self, uid: int, s: dict) -> None:
        frames = s["speech"]
        s["speech"] = []
        s["speaking"] = False
        s["silence_count"] = 0

        if len(frames) < _MIN_SPEECH_FRAMES:
            return

        wav = self._frames_to_wav(frames)
        asyncio.run_coroutine_threadsafe(self._callback(uid, wav), self._loop)

    @staticmethod
    def _frames_to_wav(frames: list[bytes]) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_RATE_VAD)
            wf.writeframes(b"".join(frames))
        buf.seek(0)
        return buf.read()
