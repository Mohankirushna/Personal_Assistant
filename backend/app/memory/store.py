"""SQLite-backed structured memory.

Phase 1 stub. Implemented in Phase 7 (Memory).

Intended design
----------------
A thin async wrapper (e.g. via `sqlite3` + a small connection helper, or
`aiosqlite`) around tables for: command history, known projects/folders,
user preferences, and remembered confirmation decisions (see
app.core.safety). Schema and migrations live here.
"""

from __future__ import annotations
