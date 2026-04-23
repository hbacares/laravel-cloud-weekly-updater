#!/usr/bin/env bash
# Install (or remove) a launchd agent that runs `wu run --due` hourly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.websiteupdater.hourly.plist"
LABEL="com.websiteupdater.hourly"

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ -f "$DEST" ]]; then
    launchctl unload "$DEST" 2>/dev/null || true
    rm "$DEST"
    echo "✓ Removed websiteupdater launchd agent."
  else
    echo "No launchd agent found at $DEST"
  fi
  exit 0
fi

# Verify venv exists
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Python venv not found — run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

# Ensure data directory exists
mkdir -p "$ROOT/data"

# Generate plist dynamically with correct paths
cat > "$DEST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/run-due.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$ROOT</string>

    <key>StandardOutPath</key>
    <string>$ROOT/data/launchd.log</string>

    <key>StandardErrorPath</key>
    <string>$ROOT/data/launchd.log</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
EOF

chmod 644 "$DEST"

# Unload if already loaded (in case of update)
launchctl unload "$DEST" 2>/dev/null || true

# Load the agent
launchctl load "$DEST"

echo "✓ Installed launchd agent: $LABEL"
echo
echo "The agent will run hourly at the top of each hour (XX:00)."
echo
echo "Useful commands:"
echo "  launchctl list | grep websiteupdater    # check if running"
echo "  launchctl start $LABEL                  # run now (for testing)"
echo "  tail -f $ROOT/data/launchd.log          # view logs"
echo "  $0 --uninstall                          # remove agent"
