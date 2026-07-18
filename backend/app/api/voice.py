"""Voice endpoints.

WS /ws/voice — bidirectional voice loop:
    client -> server: binary frames (16kHz mono PCM16), or JSON control:
                      {"type": "start_listening"}   push-to-talk
                      {"type": "say", "text": ...}  direct TTS
    server -> client: {"type": "wake", "score"}     wake word heard
                      {"type": "listening"}
                      {"type": "transcript", "text"}
                      {"type": "nothing_heard"}
                      {"type": "reply", "session_id", "text"}
                      binary WAV message             spoken reply audio
                      {"type": "audio_end"}
                      {"type": "error", "message"}

POST /voice/transcribe — multipart WAV upload -> {"text": ...} (debug/tests)
POST /voice/speak — {"text": ...} -> WAV bytes (debug/tests)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.ollama_client import ModelNotFoundError, OllamaUnavailableError
from app.core.safety import ConfirmationRequest
from app.speech.session import (
    ListeningStarted,
    NothingHeard,
    UtteranceReady,
    VoiceSession,
    WakeDetected,
)
from app.speech.stt import pcm16_to_float32

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

router = APIRouter()


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1)


def _wav_to_float32(data: bytes) -> np.ndarray:
    """Decode a 16-bit PCM WAV into mono float32 at its native rate."""
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        if wav_file.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM WAV")
        frames = wav_file.readframes(wav_file.getnframes())
        channels = wav_file.getnchannels()
    audio = pcm16_to_float32(frames)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


@router.post("/voice/transcribe")
async def transcribe(request: Request, file: UploadFile) -> dict[str, str]:
    audio = _wav_to_float32(await file.read())
    text = await request.app.state.stt.transcribe(audio)
    return {"text": text}


@router.post("/voice/speak")
async def speak(request: Request, payload: SpeakRequest) -> Response:
    wav_bytes = await request.app.state.tts.synthesize(payload.text)
    return Response(content=wav_bytes, media_type="audio/wav")


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket) -> None:
    state = websocket.app.state
    settings = state.settings
    session = VoiceSession(
        wake_detector=state.wake_detector,
        wake_threshold=settings.wake_threshold,
        silence_ms=settings.vad_silence_ms,
        energy_threshold=settings.vad_energy_threshold,
        max_utterance_seconds=settings.max_utterance_seconds,
    )
    chat_session = state.chat_service.open_session(None)
    await websocket.accept()

    async def voice_confirmer(request: ConfirmationRequest) -> bool:
        """Wait for an explicit Allow/Deny click from the floating overlay."""
        await websocket.send_json(
            {
                "type": "confirm_request",
                "tool": request.tool,
                "risk": request.risk.value,
                "action": request.action,
            }
        )
        try:
            while True:
                message = await asyncio.wait_for(websocket.receive(), timeout=120)
                if message.get("text") is None:
                    # The microphone remains live while the user clicks; those
                    # frames are not part of the already-completed command.
                    continue
                control = json.loads(message["text"])
                if control.get("type") == "confirm_response":
                    return bool(control.get("approved"))
        except (TimeoutError, WebSocketDisconnect):
            return False

    async def handle_utterance(audio: np.ndarray) -> None:
        text = await state.stt.transcribe(audio)
        if not text:
            await websocket.send_json({"type": "nothing_heard"})
            return
        await websocket.send_json({"type": "transcript", "text": text})
        try:
            reply = await state.chat_service.respond(
                chat_session, text, confirmer=voice_confirmer
            )
        except (OllamaUnavailableError, ModelNotFoundError) as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            return
        await websocket.send_json(
            {"type": "reply", "session_id": chat_session.id, "text": reply}
        )
        await speak_out(reply)

    async def speak_out(text: str) -> None:
        wav_bytes = await state.tts.synthesize(text)
        await websocket.send_bytes(wav_bytes)
        await websocket.send_json({"type": "audio_end"})

    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                for event in session.push(message["bytes"]):
                    match event:
                        case WakeDetected(score=score):
                            await websocket.send_json({"type": "wake", "score": score})
                        case ListeningStarted():
                            await websocket.send_json({"type": "listening"})
                        case NothingHeard():
                            await websocket.send_json({"type": "nothing_heard"})
                        case UtteranceReady(audio=audio):
                            await handle_utterance(audio)
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except ValueError:
                    await websocket.send_json({"type": "error", "message": "invalid JSON"})
                    continue
                if control.get("type") == "start_listening":
                    for event in session.force_listen():
                        if isinstance(event, ListeningStarted):
                            await websocket.send_json({"type": "listening"})
                elif control.get("type") == "say" and control.get("text"):
                    await speak_out(str(control["text"]))
                else:
                    await websocket.send_json(
                        {"type": "error", "message": "unknown control message"}
                    )
            elif message.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        logger.debug("voice websocket disconnected")
