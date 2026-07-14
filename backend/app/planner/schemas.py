"""Pydantic schemas for the planner's structured output.

Phase 1 stub. Implemented in Phase 5 (Planner).

Intended design
----------------
  - `ToolCall`: tool name + validated JSON-schema-typed arguments.
  - `Plan`: ordered list of `ToolCall`s plus the originating utterance and any
    memory context used to build it.
  - `ToolResult`: structured outcome of executing a `ToolCall` (success/error,
    payload, human-readable summary) fed back to the LLM for the final
    natural-language response.

These schemas are the contract the LLM's proposals are validated against
before anything is allowed to execute — see docs/ARCHITECTURE.md section 3.
"""

from __future__ import annotations
