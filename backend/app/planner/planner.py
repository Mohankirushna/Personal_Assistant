"""The Planner: utterance -> validated structured plan.

Phase 1 stub. Implemented in Phase 5.

Intended design
----------------
`Planner.build_plan(utterance: str) -> Plan`:
  1. Pull relevant context from memory (recent commands, active project,
     preferences) via app.memory.
  2. Prompt the LLM (via ModelManager-managed Ollama session) to propose a
     sequence of tool calls, constrained to the tool registry's JSON schemas
     (see app/tools/registry.py).
  3. Validate the LLM's proposal against `planner.schemas.Plan` — reject/retry
     on schema violations rather than passing through unvalidated text.
  4. Return the validated `Plan` for the safety gate and executor to consume.

The Planner never executes tool calls itself; see docs/ARCHITECTURE.md
section 3 for the full request-flow invariant.
"""

from __future__ import annotations
