"""ChromaDB-backed semantic memory.

Phase 1 stub. Implemented in Phase 7 (Memory).

Intended design
----------------
Local, persistent Chroma collection(s) for conversation turns and project
context, embedded via app.memory.embeddings. Exposes
`async def query(text: str, k: int) -> list[MemoryHit]` used by the Planner to
pull relevant context before building a plan (see docs/ARCHITECTURE.md
section 7).
"""

from __future__ import annotations
