"""Wake-word detection via openWakeWord.

Uses the pretrained "hey_jarvis" model with ONNX inference (tflite wheels are
unreliable on macOS arm64). Model files (~few MB) are downloaded once by
`scripts/install_models.sh --voice` (or on demand) and cached; detection then
runs fully offline. Small enough to stay resident continuously — not managed
by the ModelManager (see ADR 0001).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

# openWakeWord processes 16kHz int16 audio in 80ms hops.
FRAME_SAMPLES = 1280


class WakeDetector(Protocol):
    """Interface the voice pipeline depends on (real model or test fake)."""

    def feed(self, frame: np.ndarray) -> float:
        """Feed one int16 frame; returns the wake score in [0, 1]."""
        ...

    def reset(self) -> None: ...


class OpenWakeWord:
    """Lazy-loading openWakeWord detector for a single wake word."""

    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5) -> None:
        self._model_name = model_name
        self.threshold = threshold
        self._model = None

    def _ensure_loaded(self) -> Any:  # Model isn't importable at type time
        if self._model is None:
            import openwakeword
            from openwakeword.model import Model

            # Fetch the shared feature models + our wake model if missing.
            openwakeword.utils.download_models(model_names=[self._model_name])
            logger.info("Loading wake-word model %s", self._model_name)
            self._model = Model(
                wakeword_models=[self._model_name], inference_framework="onnx"
            )
        return self._model

    async def preload(self) -> None:
        await asyncio.to_thread(self._ensure_loaded)

    def feed(self, frame: np.ndarray) -> float:
        model = self._ensure_loaded()
        scores = model.predict(frame)
        # `predict` returns {model_key: score}; single-model setup -> max.
        return float(max(scores.values()))

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()
