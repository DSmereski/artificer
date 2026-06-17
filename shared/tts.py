"""
Text-to-Speech using microsoft/speecht5_tts (fast, popular HuggingFace TTS).
Converts text to a WAV bytes buffer suitable for discord.py FFmpegPCMAudio.
"""

import io
import os
from pathlib import Path

import torch
import numpy as np
import soundfile as sf
from transformers import (
    SpeechT5ForTextToSpeech,
    SpeechT5HifiGan,
    SpeechT5Processor,
    SpeechT5Tokenizer,
    SpeechT5FeatureExtractor,
)

_EMBEDDING_PATH = str(
    Path(__file__).resolve().parent.parent / "config" / "speaker_embedding.npy"
)


class SpeechT5TTS:
    MODEL_ID = "microsoft/speecht5_tts"
    VOCODER_ID = "microsoft/speecht5_hifigan"

    def __init__(self, device: str | None = None):
        if device is None:
            device = "cuda:1" if torch.cuda.is_available() else "cpu"
        self.device = device

        print(f"[TTS] Loading {self.MODEL_ID} on {device}...")
        tokenizer = SpeechT5Tokenizer.from_pretrained(self.MODEL_ID)
        feature_extractor = SpeechT5FeatureExtractor.from_pretrained(self.MODEL_ID)
        self._processor = SpeechT5Processor(
            tokenizer=tokenizer, feature_extractor=feature_extractor
        )
        self._model = SpeechT5ForTextToSpeech.from_pretrained(self.MODEL_ID).to(device)
        self._vocoder = SpeechT5HifiGan.from_pretrained(self.VOCODER_ID).to(device)

        embedding = np.load(_EMBEDDING_PATH)
        self._speaker_embedding = torch.tensor(embedding).unsqueeze(0).to(device)

        print("[TTS] Ready.")

    _MAX_TTS_CHARS = 180

    def synthesize(self, text: str) -> bytes:
        if len(text) > self._MAX_TTS_CHARS:
            truncated = text[:self._MAX_TTS_CHARS]
            for sep in (". ", "! ", "? ", ", "):
                idx = truncated.rfind(sep)
                if idx > self._MAX_TTS_CHARS // 2:
                    truncated = truncated[:idx + 1]
                    break
            text = truncated

        inputs = self._processor(text=text, return_tensors="pt").to(self.device)

        with torch.no_grad():
            speech = self._model.generate_speech(
                inputs["input_ids"],
                self._speaker_embedding,
                vocoder=self._vocoder,
            )

        audio_np = speech.cpu().numpy()

        buf = io.BytesIO()
        sf.write(buf, audio_np, samplerate=16_000, format="WAV")
        buf.seek(0)
        return buf.read()

    def synthesize_to_file(self, text: str, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.synthesize(text))
