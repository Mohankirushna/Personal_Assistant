"""Piper TTS engine (optional; requires the `tts-piper` extra and a voice).

Voice models (.onnx + .json) are downloaded by scripts/install_models.sh
--voice, or manually from https://github.com/rhasspy/piper/blob/master/VOICES.md
"""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path
from typing import Any


def piper_available(voice_path: str | None) -> bool:
    if not voice_path or not Path(voice_path).exists():
        return False
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


class PiperTTS:
    def __init__(self, voice_path: str | None) -> None:
        if not voice_path:
            raise ValueError("PiperTTS requires piper_voice_path to be set")
        self._voice_path = voice_path
        self._voice = None

    def _ensure_loaded(self) -> Any:
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self._voice_path)
        return self._voice

    async def synthesize(self, text: str) -> bytes:
        def _run() -> bytes:
            voice = self._ensure_loaded()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                voice.synthesize(text, wav_file)
            return buffer.getvalue()

        return await asyncio.to_thread(_run)
