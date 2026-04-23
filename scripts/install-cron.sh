#!/usr/bin/env bash
# Install (or remove) an hourly crontab entry that runs `wu run --due`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
MARKER="# websiteupdater"
CRON_LINE="0 * * * * cd \"$ROOT\" && $PY -m websiteupdater run --due >> $ROOT/data/cron.log 2>&1 $MARKER"

if [[ "${1:-}" == "--uninstall" ]]; then
  (crontab -l 2>/dev/null | grep -v "$MARKER") | crontab -
  echo "Removed websiteupdater cron entry."
  exit 0
fi

if [[ ! -x "$PY" ]]; then
  echo "Python venv not found at $PY — run ./scripts/bootstrap.sh first." >&2
  exit 1
fi

mkdir -p "$ROOT/data"

# Remove any existing marker line, then append the fresh one.
(crontab -l 2>/dev/null | grep -v "$MARKER"; echo "$CRON_LINE") | crontab -

echo "Installed cron entry:"
echo "  $CRON_LINE"
echo
echo "Verify with: crontab -l"
echo "Uninstall with: $0 --uninstall"
