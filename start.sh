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

# Log all output so we can diagnose autostart failures
exec >> "$SCRIPT_DIR/startup.log" 2>&1
echo "--- start.sh launched at $(date) ---"

# Ensure the X display is set (autostart doesn't always inherit it)
export DISPLAY=:0

# Give the desktop session a moment to fully initialise before touching the display
sleep 5

# Disable screen blanking and power management for an always-on display
# (|| true prevents set -e from aborting if xset isn't supported)
xset s off     || true
xset -dpms     || true
xset s noblank || true

# Note: the mouse cursor is hidden via a transparent XCURSOR theme, NOT here.
# This Pi runs the labwc Wayland compositor, where X11 tools like unclutter/xset
# do nothing. See scripts/hide-cursor.sh and the "Hiding the Mouse Cursor"
# section of ONBOARDING.md.

# Activate the virtual environment and start Flask in the background
source venv/bin/activate
python mbta_tracker.py &
FLASK_PID=$!

# Give Flask a moment to bind its port before opening the browser
sleep 4

# Kill Flask whenever this script exits for any reason (crash, signal, etc.)
trap 'kill "$FLASK_PID" 2>/dev/null || true' EXIT

# Launch Chromium in a restart loop.
# On Raspberry Pi, Chromium occasionally crashes with a "Broken pipe" display error.
# The loop recovers automatically so the kiosk never goes permanently blank.
# --disable-gpu disables hardware acceleration, which prevents the most common Pi GPU crash.
while true; do
    chromium \
        --kiosk \
        --password-store=basic \
        --noerrdialogs \
        --disable-infobars \
        --disable-session-crashed-bubble \
        --disable-restore-session-state \
        --no-first-run \
        --incognito \
        --disable-gpu \
        http://localhost:5000 || true
    echo "[$(date)] Chromium exited — restarting in 5s…"
    sleep 5
done
