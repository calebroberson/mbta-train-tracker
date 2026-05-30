#!/bin/bash
# hide-cursor.sh — Hide the mouse cursor on a Raspberry Pi OS (Trixie+) kiosk
# running the labwc Wayland compositor.
#
# Why this exists:
#   Raspberry Pi OS Trixie runs the *labwc Wayland* compositor, not X11. Every
#   X11-era cursor-hiding trick — `unclutter`, `xset`, CSS `cursor: none`, and the
#   lightdm `xserver-command=X -nocursor` option — silently does nothing under
#   Wayland. The reliable fix is to install a fully *transparent* XCURSOR theme,
#   which both labwc and Xwayland honor, so the cursor bitmap disappears no matter
#   which layer draws it.
#
# What it does:
#   1. Installs xcursorgen (shipped in the x11-apps package on Trixie).
#   2. Generates a 32x32 fully-transparent PNG using pure Python (no image libs).
#   3. Compiles it into an Xcursor file and installs it as a theme: ~/.icons/blank.
#   4. Points the labwc session at it via ~/.config/labwc/environment.
#
# This script is idempotent — safe to re-run. A reboot (or session restart) is
# required afterwards for the change to take effect.

set -e

# xcursorgen lives in the x11-apps package on Trixie (it is no longer standalone)
if ! command -v xcursorgen >/dev/null 2>&1; then
    echo "Installing xcursorgen (x11-apps)…"
    sudo apt-get update
    sudo apt-get install -y x11-apps
fi

# 1. Generate a 32x32 fully-transparent PNG (pure Python — no image libraries needed)
python3 - <<'PY'
import zlib, struct
w = h = 32
raw = b''.join(b'\x00' + b'\x00\x00\x00\x00' * w for _ in range(h))  # transparent RGBA rows
def chunk(t, d):
    return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
png = (b'\x89PNG\r\n\x1a\n'
       + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw))
       + chunk(b'IEND', b''))
open('/tmp/blank.png', 'wb').write(png)
PY

# 2. Compile the PNG into an Xcursor file
echo '32 0 0 /tmp/blank.png' > /tmp/blank.cursor
xcursorgen /tmp/blank.cursor /tmp/blank

# 3. Install it as a cursor theme named "blank", mapping every cursor role to blank
mkdir -p ~/.icons/blank/cursors
printf '[Icon Theme]\nName=blank\nComment=Transparent cursor\n' > ~/.icons/blank/index.theme
cp /tmp/blank ~/.icons/blank/cursors/default
cd ~/.icons/blank/cursors
for n in left_ptr arrow top_left_arrow right_ptr xterm text ibeam hand1 hand2 \
         pointer pointing_hand grab grabbing fleur move watch left_ptr_watch \
         progress crosshair cross size_all size_hor size_ver sb_h_double_arrow \
         sb_v_double_arrow not-allowed forbidden question_arrow help \
         n-resize s-resize e-resize w-resize ne-resize nw-resize se-resize sw-resize \
         col-resize row-resize; do ln -sf default "$n"; done

# 4. Point labwc (and Xwayland) at the transparent theme
mkdir -p ~/.config/labwc
ENV_FILE="$HOME/.config/labwc/environment"
grep -q '^XCURSOR_THEME=' "$ENV_FILE" 2>/dev/null || echo 'XCURSOR_THEME=blank' >> "$ENV_FILE"
grep -q '^XCURSOR_SIZE='  "$ENV_FILE" 2>/dev/null || echo 'XCURSOR_SIZE=24'    >> "$ENV_FILE"

echo
echo "Transparent cursor theme installed and labwc configured."
echo "Reboot (or log out and back in) for the cursor to disappear:"
echo "    sudo reboot"
