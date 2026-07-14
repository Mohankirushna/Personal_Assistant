"""Application settings.

Phase 1 stub. Implemented in Phase 2 as a `pydantic-settings` `BaseSettings`
subclass covering: Ollama host/port, default/power-mode LLM model names, the
loopback port and auth token for the backend, model RAM thresholds used by the
ModelManager, and paths for SQLite/ChromaDB data (see docs/ARCHITECTURE.md).
"""

from __future__ import annotations
