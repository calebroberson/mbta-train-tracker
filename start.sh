#!/bin/bash
# Launches the MBTA tracker Flask server and opens Chromium in kiosk mode.
#
# To run automatically on Pi OS Desktop boot, create this autostart entry:
#   mkdir -p ~/.config/autostart
#   cat > ~/.config/autostart/mbta-tracker.desktop << EOF
#   [Desktop Entry]
#   Type=Application
#   Name=MBTA Tracker
#   Exec=/home/pi/mbta-train-tracker/start.sh
#   EOF

set -e

# Resolve the project root relative to this script so it works from any cwd
SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
cd "$SCRIPT_DIR"

# Disable screen blanking and power management for an always-on display
xset s off
xset -dpms
xset s noblank

# Activate the virtual environment and start Flask in the background
source venv/bin/activate
python mbta_tracker.py &
FLASK_PID=$!

# Give Flask a moment to bind its port before opening the browser
sleep 4

# Launch Chromium in kiosk mode (no address bar, no title bar, no error dialogs)
chromium \
    --kiosk \
    --password-store=basic \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --no-first-run \
    --incognito \
    http://localhost:5000

# If Chromium exits (e.g. user closes it), shut down the Flask server too
kill "$FLASK_PID" 2>/dev/null || true
