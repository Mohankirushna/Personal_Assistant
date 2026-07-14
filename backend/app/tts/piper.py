"""Text-to-speech via Piper.

Phase 1 stub. Implemented in Phase 4 (Voice).

Intended design
----------------
Wraps the Piper ONNX runtime to synthesize the Planner's final natural-
language response into audio, streamed back to the SwiftUI app over the voice
WebSocket (app.api.voice) in chunks for low-latency playback.
"""

from __future__ import annotations
