"""Shared pytest fixtures.

Phase 1 stub. Implemented starting Phase 2 (a `TestClient`/`httpx.AsyncClient`
fixture for the FastAPI app) and extended in later phases with fixtures for a
temporary SQLite/Chroma data dir, a fake ModelManager, and a fake Ollama
client so tool/planner tests don't require a running Ollama instance.
"""

from __future__ import annotations
