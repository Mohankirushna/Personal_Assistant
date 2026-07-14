"""ModelManager: enforces the single-heavy-model RAM budget.

Phase 1 stub. See docs/adr/0001-model-manager-ram-budget.md for the full
rationale. Implemented in Phase 2.

Intended design
----------------
A small async state machine with states roughly:

    IDLE -> LLM_LOADED -> VISION_LOADED -> ...

Rules to implement:
  - Only one of {text LLM, vision model} may be loaded via Ollama at a time.
    Requesting the other unloads the current one first (Ollama keep_alive=0 /
    explicit unload call).
  - STT (whisper.cpp), TTS (Piper), and wake-word (openWakeWord) are NOT
    managed by this state machine — they are small enough to stay resident
    continuously.
  - Default LLM: Qwen2.5 3B Instruct. A "power mode" setting allows switching
    to Qwen2.5 7B Instruct.
  - Vision (Qwen2.5-VL) is only ever requested on-demand, never proactively.

Public API to implement:
  - `async def ensure_llm_loaded(power_mode: bool = False) -> None`
  - `async def ensure_vision_loaded() -> None`
  - `async def release_all() -> None`
  - `current_state` property
"""

from __future__ import annotations
