"""Speech-to-text via faster-whisper (CTranslate2 Whisper).

`base.en` int8 keeps the resident footprint around ~200MB, small enough to
stay loaded alongside the LLM on 8GB hardware (see ADR 0001 — STT is *not*
managed by the ModelManager for exactly this reason). The model file
(~75MB) is downloaded from Hugging Face on first use and cached locally;
after that, transcription is fully offline.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


class Transcriber(Protocol):
    """Interface the voice pipeline depends on (real Whisper or test fake)."""

    async def transcribe(self, audio: np.ndarray) -> str: ...


class WhisperSTT:
    """Lazy-loading faster-whisper transcriber.

    The model loads on the first transcribe call (not at import/startup),
    so backends that never receive voice traffic pay nothing.
    """

    def __init__(self, model_name: str = "base.en", compute_type: str = "int8") -> None:
        self._model_name = model_name
        self._compute_type = compute_type
        self._model: Any = None
        self._load_lock = asyncio.Lock()

    async def _ensure_loaded(self) -> Any:  # WhisperModel isn't importable at type time
        if self._model is None:
            async with self._load_lock:
                if self._model is None:
                    logger.info(
                        "Loading whisper model %s (%s)", self._model_name, self._compute_type
                    )

                    def _load() -> Any:
                        from faster_whisper import WhisperModel

                        return WhisperModel(
                            self._model_name, device="cpu", compute_type=self._compute_type
                        )

                    self._model = await asyncio.to_thread(_load)
        return self._model

    async def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe mono float32 audio at 16kHz to text."""
        model = await self._ensure_loaded()

        def _run() -> str:
            segments, _info = model.transcribe(audio, language="en", beam_size=1)
            return " ".join(segment.text.strip() for segment in segments).strip()

        return await asyncio.to_thread(_run)


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    """Convert little-endian 16-bit PCM bytes to float32 in [-1, 1]."""
    import numpy as np

    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
