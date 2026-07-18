#!/usr/bin/env bash
# Install (or refresh) Jarvis's per-user login item on macOS.
#
# LaunchAgents are used instead of relying only on SMAppService registration:
# an ad-hoc-signed development build may be rejected by Login Items, and that
# rejection is otherwise invisible after a reboot.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Jarvis login items are supported only on macOS." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$REPO_ROOT/frontend/dist/Jarvis.app}"
LABEL="dev.jarvis.assistant"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BACKEND_LABEL="dev.jarvis.assistant.backend"
BACKEND_PLIST="$HOME/Library/LaunchAgents/$BACKEND_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Jarvis"
USER_ID="$(id -u)"

if [[ ! -d "$APP" || ! -x "$APP/Contents/MacOS/Jarvis" ]]; then
    echo "Jarvis.app was not found at: $APP" >&2
    echo "Build it first with: scripts/make_app.sh" >&2
    exit 1
fi

# launchd does not start in the project's working directory; its executable
# path must therefore be absolute. Run the bundle executable directly: `open`
# returns immediately and can leave a login-time launch silently unobserved.
APP="$(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"

mkdir -p "$(dirname "$PLIST")"
mkdir -p "$LOG_DIR"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP/Contents/MacOS/Jarvis</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <!-- This is a menu-bar GUI process, not a background daemon. -->
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>LimitLoadToSessionType</key>
    <array>
        <string>Aqua</string>
    </array>
    <!-- Keep diagnostics from a login-time launch available after reboot. -->
    <key>StandardOutPath</key>
    <string>$LOG_DIR/login-item.out.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/login-item.err.log</string>
</dict>
</plist>
PLIST

# Refresh the app job if it is already loaded.  The app owns the backend: it
# attaches to one already running, or starts exactly one itself.  Keeping a
# separate backend LaunchAgent races that startup and can leave the app
# talking to the wrong authenticated process.
launchctl bootout "gui/$USER_ID/$LABEL" 2>/dev/null || true
launchctl bootout "gui/$USER_ID/$BACKEND_LABEL" 2>/dev/null || true
rm -f "$BACKEND_PLIST"
launchctl bootstrap "gui/$USER_ID" "$PLIST"

echo "Jarvis will start automatically when you log in and will start its backend."
echo "App login item: $PLIST"
echo "Startup log: $LOG_DIR/login-item.{out,err}.log"
