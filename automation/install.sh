#!/bin/bash
# Install the nightly launchd job.
set -euo pipefail
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.stocksignals.daily.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/logs"
sed "s|__PROJECT__|$PROJECT|g" "$PROJECT/automation/com.stocksignals.daily.plist" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "Installed. Runs weekdays at 16:30 local time."
echo "Test it now with:  launchctl start com.stocksignals.daily"
echo "Remove it with:    launchctl unload $PLIST"
