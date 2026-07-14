"""Speech-to-text via whisper.cpp.

Phase 1 stub. Implemented in Phase 4 (Voice).

Intended design
----------------
Wraps whisper.cpp (Metal-accelerated on Apple Silicon), model size
`base.en`/`small.en` quantized, via a Python binding or subprocess. Consumes
streamed audio chunks from the voice WebSocket (app.api.voice) and yields
incremental transcription results.
"""

from __future__ import annotations
