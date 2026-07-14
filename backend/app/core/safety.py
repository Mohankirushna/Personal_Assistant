"""Safety / confirmation gate.

Phase 1 stub. See docs/ARCHITECTURE.md section 5. Implemented starting Phase 5
(Planner) / Phase 6 (Tools), once there are real tool calls to gate.

Intended design
----------------
Defines the `RiskLevel` enum (`SAFE`, `SENSITIVE`, `DESTRUCTIVE`) that every
`Tool.execute()` call is annotated with (see app/tools/base.py). Before a tool
call runs, `SafetyGate.check(tool_call)` is consulted:
  - SAFE: runs immediately.
  - SENSITIVE: confirmed once per session per exact command, unless the user
    has opted into always-confirm.
  - DESTRUCTIVE: always blocks on an explicit confirmation round-trip to the
    SwiftUI app via WebSocket, showing the exact action verbatim.

Public API to implement:
  - `class RiskLevel(str, Enum)`
  - `class SafetyGate` with `async def check(self, tool_call) -> bool`
"""

from __future__ import annotations
