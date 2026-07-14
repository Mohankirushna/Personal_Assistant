"""Screen understanding via Qwen2.5-VL.

Phase 1 stub. Implemented in Phase 8 (Vision).

Intended design
----------------
Only invoked on explicit user request ("Jarvis, look at my screen"). Takes a
screenshot (via app.tools.system), requests app.core.model_manager to unload
the text LLM and load Qwen2.5-VL via Ollama, runs inference to identify
on-screen context (VS Code, browser, terminal, error dialogs), and returns a
structured description plus suggested fixes for the Planner to relay.
"""

from __future__ import annotations
