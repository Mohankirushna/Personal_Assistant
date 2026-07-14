"""Tool auto-discovery registry.

Phase 1 stub. Implemented in Phase 6 (Tools), extended for plugins alongside
Phase 6/9.

Intended design
----------------
At startup:
  1. Import every submodule under `app.tools` (finder, terminal, browser, git,
     vscode, vision, clipboard, system) and register any `Tool` subclasses
     found.
  2. Scan `app.plugins` for additional installable packages exposing `Tool`
     subclasses using the same interface (see app/tools/base.py) and register
     them identically — first-party and community tools are not special-cased.
  3. Expose `get_tool(name) -> Tool` and `list_tools() -> list[Tool]` used by
     the Planner and the `/tools` API router.

See docs/ARCHITECTURE.md section 6 for the plugin system design.
"""

from __future__ import annotations
