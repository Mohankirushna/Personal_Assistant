"""TTS engine selection.

Two implementations behind one protocol:
  - PiperTTS: neural voices, fully open source (preferred when installed —
    `uv sync --extra tts-piper` — and a voice model file is configured).
  - SayTTS: macOS's built-in `say` command. Zero dependencies, offline,
    always available on the target platform; the default fallback.

`build_tts_engine(settings)` picks per the `tts_engine` setting
("auto" | "piper" | "say").
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class TTSEngine(Protocol):
    """Synthesize `text` and return WAV bytes (16-bit PCM)."""

    async def synthesize(self, text: str) -> bytes: ...


def build_tts_engine(settings: Settings) -> TTSEngine:
    from app.tts.piper import PiperTTS, piper_available
    from app.tts.say import SayTTS

    choice = settings.tts_engine
    if choice == "piper" or (choice == "auto" and piper_available(settings.piper_voice_path)):
        logger.info("TTS engine: piper (%s)", settings.piper_voice_path)
        return PiperTTS(settings.piper_voice_path)
    logger.info("TTS engine: macOS say (voice=%s)", settings.say_voice or "system default")
    return SayTTS(voice=settings.say_voice)
