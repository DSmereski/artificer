"""WS /v1/voice/{bot} tests. Uses a fake voice pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from gateway.voice_shim import PipelineResult


@dataclass
class _FakeLLM:
    def chat(self, user_id: int, text: str) -> str:
        return f"echo: {text}"


class _FakePipeline:
    def run_pipeline(self, *, wav_bytes, llm_reply, user_id):
        transcript = "hello world"
        reply = llm_reply(user_id, transcript)
        return PipelineResult(
            transcript=transcript,
            reply_text=reply,
            reply_wav=b"RIFF....WAVE",
        )


def _install(client: TestClient) -> None:
    st = client.app.state.ai_team
    st.voice_pipeline = _FakePipeline()
    # Terry adapter needs an _llm attribute with .chat(). Overwrite the fake adapter.
    class _Terry:
        name = "terry"
        display_name = "Terry"
        _llm = _FakeLLM()
        def status(self) -> str:
            return "online"
        async def reply_stream(self, user_id, text):
            yield "unused"
    adapters = dict(st.adapters)
    adapters["terry"] = _Terry()
    st.adapters = adapters


def test_voice_ws_happy_path(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _install(client)
    _, token = paired_token
    with client.websocket_connect(f"/v1/voice/terry?token={token}") as ws:
        ws.send_bytes(b"PCM-or-WAV-bytes-dont-matter-to-fake")
        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"
        assert transcript["text"] == "hello world"
        assistant = ws.receive_json()
        assert assistant["type"] == "assistant"
        assert "echo" in assistant["text"]
        audio = ws.receive_bytes()
        assert audio.startswith(b"RIFF")
        done = ws.receive_json()
        assert done["type"] == "done"


def test_voice_ws_rejects_claude_code(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _install(client)
    _, token = paired_token
    try:
        with client.websocket_connect(f"/v1/voice/claude-code?token={token}"):
            pass
    except Exception:
        return
    raise AssertionError("expected WS close for claude-code voice")


def test_voice_ws_rejects_unknown_bot(
    client: TestClient, paired_token: tuple[str, str]
) -> None:
    _install(client)
    _, token = paired_token
    try:
        with client.websocket_connect(f"/v1/voice/nobody?token={token}"):
            pass
    except Exception:
        return
    raise AssertionError("expected WS close for unknown bot")


def test_voice_ws_requires_token(client: TestClient) -> None:
    _install(client)
    try:
        with client.websocket_connect("/v1/voice/terry"):
            pass
    except Exception:
        return
    raise AssertionError("expected WS close without token")
