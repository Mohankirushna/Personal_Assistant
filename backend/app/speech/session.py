"""VoiceSession: the per-connection audio state machine.

Transport-agnostic and synchronous so it can be unit-tested by pushing PCM
bytes at it. The WebSocket handler (app.api.voice) feeds it audio and reacts
to the events it emits; slow work (STT, LLM, TTS) happens in the handler, not
here.

States:

    WAITING_WAKE --wake score >= threshold--> RECORDING
    RECORDING  --trailing silence / max length--> emits UtteranceReady,
                                                  back to WAITING_WAKE
    force_listen() jumps straight to RECORDING (push-to-talk).

Audio contract: 16 kHz mono PCM16 little-endian, arbitrary chunk sizes;
internally re-framed to openWakeWord's 80 ms hop (1280 samples).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

from app.speech.stt import pcm16_to_float32
from app.speech.wake_word import FRAME_SAMPLES, WakeDetector

SAMPLE_RATE = 16_000


@dataclass
class WakeDetected:
    score: float


@dataclass
class ListeningStarted:
    pass


@dataclass
class UtteranceReady:
    audio: np.ndarray  # float32 [-1, 1] @ 16kHz


@dataclass
class NothingHeard:
    """Listening ended without enough speech to transcribe."""


VoiceEvent = WakeDetected | ListeningStarted | UtteranceReady | NothingHeard


class _State(enum.Enum):
    WAITING_WAKE = "waiting_wake"
    RECORDING = "recording"


class VoiceSession:
    def __init__(
        self,
        wake_detector: WakeDetector,
        wake_threshold: float = 0.5,
        silence_ms: int = 800,
        energy_threshold: float = 0.015,
        max_utterance_seconds: float = 15.0,
        min_speech_ms: int = 300,
    ) -> None:
        self._detector = wake_detector
        self._wake_threshold = wake_threshold
        self._silence_frames_needed = max(1, int(silence_ms / 80))
        self._energy_threshold = energy_threshold
        self._max_frames = int(max_utterance_seconds * SAMPLE_RATE / FRAME_SAMPLES)
        self._min_speech_frames = max(1, int(min_speech_ms / 80))

        self._state = _State.WAITING_WAKE
        self._pending = np.empty(0, dtype=np.int16)
        # Chunks may split mid-sample; carry the odd byte to the next push.
        self._byte_remainder = b""
        self._utterance: list[np.ndarray] = []
        self._silent_frames = 0
        self._speech_frames = 0

    @property
    def state(self) -> str:
        return self._state.value

    def force_listen(self) -> list[VoiceEvent]:
        """Push-to-talk: start recording without a wake word."""
        self._begin_recording()
        return [ListeningStarted()]

    def push(self, pcm: bytes) -> list[VoiceEvent]:
        """Feed raw PCM16 bytes; returns events triggered by this chunk."""
        events: list[VoiceEvent] = []
        data = self._byte_remainder + pcm
        usable = len(data) - (len(data) % 2)
        self._byte_remainder = data[usable:]
        chunk = np.frombuffer(data[:usable], dtype=np.int16)
        self._pending = np.concatenate([self._pending, chunk])

        while len(self._pending) >= FRAME_SAMPLES:
            frame = self._pending[:FRAME_SAMPLES]
            self._pending = self._pending[FRAME_SAMPLES:]
            events.extend(self._process_frame(frame))
        return events

    def _process_frame(self, frame: np.ndarray) -> list[VoiceEvent]:
        if self._state is _State.WAITING_WAKE:
            score = self._detector.feed(frame)
            if score >= self._wake_threshold:
                self._begin_recording()
                return [WakeDetected(score=score), ListeningStarted()]
            return []

        # RECORDING
        self._utterance.append(frame)
        rms = float(np.sqrt(np.mean(pcm16_to_float32(frame.tobytes()) ** 2)))
        if rms < self._energy_threshold:
            self._silent_frames += 1
        else:
            self._silent_frames = 0
            self._speech_frames += 1

        utterance_done = (
            self._speech_frames >= self._min_speech_frames
            and self._silent_frames >= self._silence_frames_needed
        ) or len(self._utterance) >= self._max_frames

        if not utterance_done:
            return []

        had_speech = self._speech_frames >= self._min_speech_frames
        audio = pcm16_to_float32(np.concatenate(self._utterance).tobytes())
        self._reset_to_waiting()
        return [UtteranceReady(audio=audio)] if had_speech else [NothingHeard()]

    def _begin_recording(self) -> None:
        self._state = _State.RECORDING
        self._utterance = []
        self._silent_frames = 0
        self._speech_frames = 0

    def _reset_to_waiting(self) -> None:
        self._state = _State.WAITING_WAKE
        self._utterance = []
        self._detector.reset()
