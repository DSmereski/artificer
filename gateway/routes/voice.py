"""WS /v1/voice/{bot} — PTT voice turn.

Client protocol (simple PTT for v1):
  1. Client opens WS with Bearer token (header or ?token=...).
  2. Client sends ONE binary message containing a complete WAV (any sample
     rate; server resamples).
  3. Server replies (all JSON text frames except the final audio binary):
       {"type": "transcript", "text": "..."}
       {"type": "assistant",  "text": "..."}
       <binary WAV bytes>
       {"type": "done"}
  4. Server closes, or client can send another WAV for a follow-up turn.

Continuous-call mode (v1.1) will reuse the same endpoint with a
client-side VAD segmenting utterances; server logic is unchanged.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from gateway.deps import authenticate_ws, state


router = APIRouter(prefix="/v1", tags=["voice"])
log = logging.getLogger("gateway.voice")


@router.websocket("/voice/{bot}")
async def voice_ws(websocket: WebSocket, bot: str) -> None:
    app_state = state(websocket)
    adapter = app_state.adapters.get(bot)
    if adapter is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="unknown bot")
        return
    # Only Maggy / Terry / Scout make sense for voice. Claude Code doesn't have audio.
    if bot == "claude-code":
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="no voice for claude-code")
        return

    device = await authenticate_ws(websocket, app_state)
    if device is None:
        return

    pipeline = app_state.voice_pipeline
    if pipeline is None:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="voice disabled")
        return

    await websocket.accept()

    user_id = int(hash(device.id) & 0xFFFFFFFF)

    # Pull a sync .chat method off the adapter for the pipeline. All three
    # real adapters expose ._llm (LLMClient) whose chat(user_id, text)->str
    # signature matches the voice pipeline's expectation.
    llm = getattr(adapter, "_llm", None)
    if llm is None or not hasattr(llm, "chat"):
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="bot has no text chat")
        return

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            wav_bytes = msg.get("bytes")
            if wav_bytes is None:
                await websocket.send_json(
                    {"type": "error", "message": "expected binary WAV"}
                )
                continue

            loop = asyncio.get_running_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: pipeline.run_pipeline(
                        wav_bytes=wav_bytes,
                        llm_reply=llm.chat,
                        user_id=user_id,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                log.exception("voice pipeline failed")
                await websocket.send_json(
                    {"type": "error", "message": f"pipeline: {e}"}
                )
                continue

            if not result.transcript:
                await websocket.send_json(
                    {"type": "error", "message": "no speech detected"}
                )
                continue

            await websocket.send_json({"type": "transcript", "text": result.transcript})
            await websocket.send_json({"type": "assistant", "text": result.reply_text})
            if result.reply_wav:
                await websocket.send_bytes(result.reply_wav)
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        return
