#!/bin/bash
# Removes the schedule. Leaves the bot itself alone.
set -euo pipefail

LABEL="com.assignmentbot.tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || launchctl unload "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"

echo "Schedule removed. You can still run the bot by hand any time."
