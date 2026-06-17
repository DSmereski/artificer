"""
Speech-to-Text using openai/whisper-large-v3 (most popular ASR on HuggingFace).
Transcribes raw audio bytes (WAV) into text.
"""

import io
import torch
import numpy as np
import soundfile as sf
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline


class WhisperSTT:
    MODEL_ID = "openai/whisper-large-v3"

    def __init__(self, device: str | None = None):
        if device is None:
            device = "cuda:1" if torch.cuda.is_available() else "cpu"
        self.device = device
        torch_dtype = torch.float16 if "cuda" in device else torch.float32

        print(f"[STT] Loading {self.MODEL_ID} on {device}...")
        model = AutoModelForSpeechSeq2Seq.from_pretrained(
            self.MODEL_ID,
            dtype=torch_dtype,
            low_cpu_mem_usage=True,
            use_safetensors=True,
        )
        model.to(device)

        processor = AutoProcessor.from_pretrained(self.MODEL_ID)

        self._pipe = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            torch_dtype=torch_dtype,
            device=device,
        )
        print("[STT] Ready.")

    def transcribe(self, wav_bytes: bytes) -> str:
        audio_array, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")

        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)
        if sample_rate != 16_000:
            import librosa
            audio_array = librosa.resample(audio_array, orig_sr=sample_rate, target_sr=16_000)

        result = self._pipe(
            audio_array,
            generate_kwargs={"language": "english"},
            return_timestamps=False,
        )
        return result["text"].strip()
