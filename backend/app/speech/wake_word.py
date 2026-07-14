"""Wake-word detection via openWakeWord.

Phase 1 stub. Implemented in Phase 4 (Voice).

Intended design
----------------
Runs a lightweight, always-on openWakeWord model trained/fine-tuned for the
wake word "Jarvis" against the live microphone stream. On detection, signals
app.api.voice to begin buffering audio for app.speech.stt transcription.
Deliberately kept resident continuously (see
docs/adr/0001-model-manager-ram-budget.md) since its footprint is small.
"""

from __future__ import annotations
