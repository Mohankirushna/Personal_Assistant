"""VoiceSession state machine with a scripted fake wake detector."""

from __future__ import annotations

import numpy as np

from app.speech.session import (
    ListeningStarted,
    NothingHeard,
    UtteranceReady,
    VoiceSession,
    WakeDetected,
)
from app.speech.wake_word import FRAME_SAMPLES


class FakeWake:
    """Returns queued scores, then 0.0 forever."""

    def __init__(self, scores: list[float] | None = None) -> None:
        self.scores = list(scores or [])
        self.reset_calls = 0

    def feed(self, frame: np.ndarray) -> float:
        return self.scores.pop(0) if self.scores else 0.0

    def reset(self) -> None:
        self.reset_calls += 1


def frames(count: int, loud: bool) -> bytes:
    """`count` frames of loud noise or silence as PCM16 bytes."""
    rng = np.random.default_rng(seed=42)
    if loud:
        data = (rng.uniform(-0.4, 0.4, count * FRAME_SAMPLES) * 32767).astype(np.int16)
    else:
        data = np.zeros(count * FRAME_SAMPLES, dtype=np.int16)
    return data.tobytes()


def make_session(detector: FakeWake) -> VoiceSession:
    return VoiceSession(
        wake_detector=detector,
        wake_threshold=0.5,
        silence_ms=240,  # 3 frames
        energy_threshold=0.015,
        max_utterance_seconds=2.0,
        min_speech_ms=160,  # 2 frames
    )


def test_stays_idle_below_threshold() -> None:
    session = make_session(FakeWake(scores=[0.1, 0.2, 0.3]))
    assert session.push(frames(3, loud=True)) == []
    assert session.state == "waiting_wake"


def test_wake_then_utterance() -> None:
    session = make_session(FakeWake(scores=[0.9]))
    events = session.push(frames(1, loud=True))
    assert isinstance(events[0], WakeDetected)
    assert isinstance(events[1], ListeningStarted)
    assert session.state == "recording"

    # Speech, then enough trailing silence to endpoint.
    assert session.push(frames(4, loud=True)) == []
    events = session.push(frames(3, loud=False))
    assert len(events) == 1
    assert isinstance(events[0], UtteranceReady)
    assert events[0].audio.dtype == np.float32
    assert session.state == "waiting_wake"


def test_push_to_talk_skips_wake() -> None:
    session = make_session(FakeWake())
    events = session.force_listen()
    assert isinstance(events[0], ListeningStarted)
    assert session.state == "recording"


def test_silence_only_yields_nothing_heard() -> None:
    session = make_session(FakeWake())
    session.force_listen()
    # Max utterance is 2s = 25 frames of silence -> NothingHeard at the cap.
    events = session.push(frames(25, loud=False))
    assert any(isinstance(event, NothingHeard) for event in events)
    assert session.state == "waiting_wake"


def test_detector_reset_after_utterance() -> None:
    detector = FakeWake(scores=[0.9])
    session = make_session(detector)
    session.push(frames(1, loud=True))
    session.push(frames(4, loud=True))
    session.push(frames(3, loud=False))
    assert detector.reset_calls == 1


def test_partial_chunks_are_reframed() -> None:
    """Chunks smaller than one frame accumulate correctly."""
    session = make_session(FakeWake(scores=[0.9]))
    blob = frames(1, loud=True)
    events: list = []
    third = len(blob) // 3
    for piece in (blob[:third], blob[third : 2 * third], blob[2 * third :]):
        events.extend(session.push(piece))
    assert any(isinstance(event, WakeDetected) for event in events)
