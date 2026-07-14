"""Local embedding generation.

Phase 1 stub. Implemented in Phase 7 (Memory).

Intended design
----------------
Wraps `nomic-embed-text` via Ollama as the primary embedding model, with
`sentence-transformers/all-MiniLM-L6-v2` as a pure-CPU fallback if Ollama is
unavailable. Used by app.memory.vector_store for both writes and queries.
"""

from __future__ import annotations
