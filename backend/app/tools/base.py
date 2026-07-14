"""The `Tool` interface every built-in and plugin tool must implement.

Phase 1 stub. Implemented in Phase 6 (Tools).

Intended design
----------------
An abstract base class (or `Protocol`) with:
  - `name: str` — unique tool identifier used in plans.
  - `description: str` — shown to the LLM during planning.
  - `args_schema: type[pydantic.BaseModel]` — validates tool-call arguments.
  - `risk_level: app.core.safety.RiskLevel` — may be a fixed class attribute or
    computed per-call (e.g., `terminal` risk depends on the actual command).
  - `async def execute(self, args) -> app.planner.schemas.ToolResult`

Built-in tools (finder, terminal, browser, git, vscode, vision, clipboard,
system) and user plugins (app/plugins/) both implement this same interface —
see app/tools/registry.py and docs/ARCHITECTURE.md section 6.
"""

from __future__ import annotations
