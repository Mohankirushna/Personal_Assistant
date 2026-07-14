# Frontend (macOS App)

Placeholder. The SwiftUI Xcode project is scaffolded in **Phase 3**.

## Planned shape

- Menu-bar app (`MenuBarExtra`) plus an optional full window for chat/history.
- Requests and holds the macOS permissions the assistant needs: Microphone,
  Accessibility, Screen Recording, Automation (AppleScript).
- Talks to the Python backend over localhost REST + WebSocket only (see
  [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) section 8) — the backend
  process is spawned or health-checked by the app on launch.
- Renders confirmation prompts for `sensitive`/`destructive` tool calls
  (see [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) section 5), showing
  the exact action verbatim.

No code lives here yet.
