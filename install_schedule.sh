#!/bin/bash
# Installs the hourly schedule (7 AM - 11 PM daily) as a macOS launchd agent.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.assignmentbot.tracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$DIR/run_headless.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$DIR</string>

  <!-- Every hour from 07:00 to 23:00, every day. Hourly rather than a few
       times a week because rows only appear and disappear when the bot runs:
       "due within 7 days", "gone 1 day after it was due" and "gone 30 minutes
       after you tick it off" are only true if something is actually running. -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>11</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>14</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>15</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>19</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>20</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>
  </array>

  <key>StandardOutPath</key>
  <string>$DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$DIR/logs/launchd.err.log</string>

  <key>RunAtLoad</key>
  <false/>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST

# Reload cleanly whether or not it was already installed.
launchctl bootout "gui/$UID/$LABEL" >/dev/null 2>&1 || true
if ! launchctl bootstrap "gui/$UID" "$PLIST" >/dev/null 2>&1; then
  launchctl load "$PLIST"
fi

echo "Installed: $PLIST"
echo
if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  echo "The schedule is active: every hour from 7 AM to 11 PM, daily."
else
  echo "Warning: launchd did not report the job as loaded. Check with:"
  echo "  launchctl print gui/$UID/$LABEL"
fi
echo
echo "If the Mac is asleep at a scheduled hour, launchd runs the job once it wakes."
echo "To try it right now:   launchctl kickstart -k gui/$UID/$LABEL"
echo "Then watch:            tail -f '$DIR/logs/scheduled.log'"
echo "To remove it:          '$DIR/uninstall_schedule.sh'"
