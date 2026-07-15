"""Real-model voice tests (downloads whisper/openWakeWord weights on first
run; skipped in environments without them via `-m "not integration"`).

The STT loopback uses macOS `say` to synthesize known text, then asserts
Whisper transcribes it back — no microphone or human needed.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.tts.say import SayTTS

pytestmark = pytest.mark.integration


async def test_say_tts_produces_wav() -> None:
    wav_bytes = await SayTTS().synthesize("Hello from Jarvis.")
    assert wav_bytes[:4] == b"RIFF"
    assert len(wav_bytes) > 10_000


async def test_stt_loopback_via_say() -> None:
    """say -> WAV -> Whisper -> text round trip."""
    import numpy as np

    from app.api.voice import _wav_to_float32
    from app.speech.stt import WhisperSTT

    wav_bytes = await SayTTS().synthesize("open the downloads folder")
    audio_22k = _wav_to_float32(wav_bytes)
    # say outputs 22.05kHz; whisper expects 16kHz — linear resample.
    target_length = int(len(audio_22k) * 16_000 / 22_050)
    audio_16k = np.interp(
        np.linspace(0, len(audio_22k) - 1, target_length),
        np.arange(len(audio_22k)),
        audio_22k,
    ).astype(np.float32)

    stt = WhisperSTT(model_name="base.en", compute_type="int8")
    text = await stt.transcribe(audio_16k)
    normalized = text.lower().strip(" .!,")
    assert "downloads" in normalized and "folder" in normalized, f"got: {text!r}"


async def test_wake_word_model_loads_and_scores() -> None:
    """The pretrained hey_jarvis model loads and produces sane scores."""
    import numpy as np

    from app.speech.wake_word import FRAME_SAMPLES, OpenWakeWord

    settings = Settings(_env_file=None)
    detector = OpenWakeWord(model_name=settings.wake_word_model)
    await detector.preload()

    # Silence must never trigger.
    silence = np.zeros(FRAME_SAMPLES, dtype=np.int16)
    scores = [detector.feed(silence) for _ in range(20)]
    assert all(score < 0.3 for score in scores), f"silence scored {max(scores)}"
