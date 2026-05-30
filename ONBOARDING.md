# MBTA Train Tracker — Developer Onboarding

This document covers everything a new contributor needs to understand, run, and modify the codebase.

---

## What It Does

A real-time MBTA arrival board designed to run permanently on a Raspberry Pi with a small HDMI screen. It polls the MBTA v3 API every 30 seconds and renders a full-screen kiosk display showing upcoming train arrival times across multiple stations and lines.

---

## Repository Layout

```
mbta-train-tracker/
├── mbta_tracker.py          # Entire application — Flask server + background fetch loop
├── templates/
│   └── index.html           # Single-page UI (CSS + vanilla JS)
├── start.sh                 # Pi kiosk launcher (Chromium fullscreen + Flask)
├── requirements.txt         # Runtime dependencies
├── tests/
│   ├── test_time_utils.py   # Unit tests for minutes_until()
│   ├── test_mbta_get.py     # Unit tests for HTTP retry/rate-limit logic
│   ├── test_fetch_predictions.py
│   ├── test_flask_routes.py
│   ├── test_stations.py
│   └── test_fetch_loop.py   # Integration-style tests for the prediction pipeline
└── .env                     # Not committed — holds MBTA_API_KEY
```

The entire backend lives in a **single file** (`mbta_tracker.py`). There are no submodules or packages.

---

## Architecture Overview

```
                  ┌─────────────────────────────┐
                  │         main()              │
                  │  1. Resolve station IDs     │
                  │  2. Start background thread │
                  │  3. Start Flask (blocks)    │
                  └────────────┬────────────────┘
                               │
              ┌────────────────┴─────────────────┐
              │                                  │
   ┌──────────▼──────────┐            ┌──────────▼──────────┐
   │   _fetch_loop()     │            │   Flask web server  │
   │  (daemon thread)    │            │                     │
   │                     │  lock      │  GET /              │
   │  Every 30 seconds:  │◄──────────►│  GET /data          │
   │  - Call MBTA API    │ _display_  │                     │
   │  - Process results  │   data     │  Serves index.html  │
   │  - Write to shared  │            │  + JSON snapshot    │
   │    _display_data    │            └─────────────────────┘
   └─────────────────────┘
```

- The **fetch thread** owns all API communication and data processing. It writes to a shared `_display_data` dict protected by `_display_lock`.
- The **Flask server** is read-only — it just serialises `_display_data` to JSON on each `/data` request.
- The **browser** polls `/data` every 30 seconds via `setInterval` and re-renders the page with vanilla JS.

---

## Data Flow (Step by Step)

### Startup — runs once

1. `main()` iterates `CONFIG` and calls `find_station_parent_ids_for_routes(station_name, route_ids)` for each entry.
2. That function hits `/stops?filter[route]=<id>` for each route, finds stops whose `name` matches the configured station name, and collects their `parent_station` place IDs (e.g. `place-pktrm` for Park Street).
3. The resolved targets (station display name, MBTA lookup name, parent IDs, routes, walk time) are stored in a list and passed to the fetch thread.

### Fetch loop — runs every 30 seconds

For each resolved station target:

1. **Fetch**: `fetch_predictions(parent_id, route_ids)` makes one batched call to `/predictions` with all routes comma-joined. The response includes sideloaded `trip` entities so headsigns (destination names) are available without a second API call.

2. **Process**: For each prediction record:
   - Extract route ID and the earlier of `arrival_time` / `departure_time`.
   - Convert the timestamp to minutes from now via `minutes_until()`.
   - Look up the trip's headsign from the sideloaded trip map.
   - **Filter terminal trains**: skip if the headsign matches the station name (e.g. skip "Bowdoin"-destined trains at Bowdoin — those can't be boarded).
   - Collapse all Green Line branch IDs (`Green-B/C/D/E`) under a single `"Green"` display key; append the branch letter to the headsign for disambiguation (e.g. `"Boston College (B)"`).
   - Accumulate `(minutes, headsign)` tuples into per-route `buckets`.

3. **Finalise**: For each bucket, deduplicate identical `(mins, headsign)` pairs, drop trains arriving sooner than `min_walk_mins`, sort ascending, cap at `MAX_PREDICTIONS_PER_BUCKET`, then convert to JSON-serialisable dicts.

4. Write the full `stations_out` list to `_display_data` under the lock.

### Display

The browser fetches `/data` every 30 seconds, builds HTML from the JSON with `buildStation` / `buildRoute` / `buildArrival` helpers, and injects it into the DOM. No page reload required.

---

## Configuration

All user-facing settings live at the top of `mbta_tracker.py`.

### Adding a Station

```python
CONFIG = [
    {"station_name": "Bowdoin", "routes": ["Blue"], "min_walk_mins": 2},
    # Add a new entry:
    {"station_name": "Downtown Crossing", "routes": ["Orange", "Red"], "min_walk_mins": 5},
]
```

| Field | Required | Description |
|---|---|---|
| `station_name` | Yes | Must match the MBTA stop name exactly (checked case-insensitively). Also used to filter out terminal trains — see below. |
| `routes` | Yes | List of MBTA route IDs. Valid values: `"Blue"`, `"Orange"`, `"Red"`, `"Green-B"`, `"Green-C"`, `"Green-D"`, `"Green-E"`. |
| `min_walk_mins` | No (default 0) | Trains arriving in fewer than this many minutes are hidden (not enough time to walk to the station). |
| `display_name` | No | Override what's shown in the UI. Useful when the same station appears twice in CONFIG for different lines (e.g. Park Street Red vs. Park Street Green). When set, `station_name` is still used internally for stop ID resolution and terminal-train filtering. |

### Splitting One Station Into Multiple Columns

Park Street is tracked twice — once for Red, once for Green — producing two columns on the display:

```python
{"station_name": "Park Street", "routes": ["Red"],                                    "min_walk_mins": 6},
{"station_name": "Park Street", "routes": ["Green-B", "Green-C", "Green-D", "Green-E"], "min_walk_mins": 6},
```

Both entries share the same `station_name` (so the line color dot distinguishes them visually). If you ever need different labels, add `display_name`.

### Other Tunable Constants

```python
POLL_SECONDS = 30              # How often the fetch thread wakes up
MAX_PREDICTIONS_PER_BUCKET = 8 # Max arrivals shown per route per station card
HTTP_TIMEOUT = 15              # Seconds before an MBTA API request is aborted
```

---

## Key Functions Reference

| Function | Purpose |
|---|---|
| `mbta_get(path, params)` | Thin HTTP client with 3-attempt retry, 429 rate-limit handling, and safe empty-response fallback. All MBTA calls go through here. |
| `find_station_parent_ids_for_routes(name, routes)` | One-time startup lookup: translates a human station name into MBTA `place-*` parent IDs by querying `/stops`. |
| `fetch_predictions(stop_id, route_ids)` | Per-poll API call: fetches `/predictions` for a parent stop with all routes batched in one request; sideloads trip headsigns. |
| `minutes_until(iso_str, now=None)` | Converts an ISO 8601 timestamp to whole minutes from now. Returns `None` on bad input; clamps negatives to 0. Accepts an optional `now` for test injection. |
| `_fetch_loop(resolved_targets)` | Background thread body. Runs forever, processes predictions, writes `_display_data`. Catches all exceptions so the thread never silently dies. |
| `main()` | Entry point: resolves station IDs, starts the fetch thread, runs Flask. |

---

## Local Development Setup

```bash
# Clone and create a virtual environment
git clone https://github.com/calebroberson/mbta-train-tracker.git
cd mbta-train-tracker
python3 -m venv venv

# Activate (macOS/Linux)
source venv/bin/activate
# Activate (Windows PowerShell)
venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Optional: add your MBTA API key (improves rate limits)
echo "MBTA_API_KEY=your_key_here" > .env

# Run the server
python mbta_tracker.py
# Open http://localhost:5000
```

Get a free API key at [api.mbta.com](https://api.mbta.com). The app works without one but may hit rate limits under heavy polling.

---

## Running Tests

```bash
# All tests
pytest tests/

# A single file
pytest tests/test_fetch_loop.py

# With coverage report
pytest tests/ --cov=mbta_tracker --cov-report=term-missing
```

### Test Stack

| Library | Role |
|---|---|
| `pytest` | Test runner |
| `responses` | HTTP mocking — intercepts `requests` calls without hitting the real API |
| `freezegun` | Time mocking — freezes `datetime.now()` so time-sensitive assertions are deterministic |

### Test File Summary

| File | What It Tests |
|---|---|
| `test_time_utils.py` | `minutes_until()` — None/empty/unparsable inputs, floor math, Z-suffix, past timestamps |
| `test_mbta_get.py` | Retry logic, 429 rate-limit with and without `Retry-After`, HTTP 500 exhaustion |
| `test_fetch_predictions.py` | Data/included return shape, API failure fallback, single-request route batching |
| `test_flask_routes.py` | `/` returns 200, `/data` returns valid JSON, error state propagation |
| `test_stations.py` | `find_station_parent_ids_for_routes` — deduplication, not-found case |
| `test_fetch_loop.py` | Full prediction pipeline: bucket building, Green branch collapse, walk-time filter, terminal-station filter, route ordering, arrivals cap, error handling |

The fetch loop tests run `_fetch_loop` for exactly one iteration by patching `time.sleep` with `side_effect=SystemExit`.

---

## Pi Deployment

### How It Runs on the Pi

`start.sh` is the entry point. It:
1. Disables screen blanking (`xset s off; xset -dpms; xset s noblank`)
2. Activates the venv and starts `python mbta_tracker.py` in the background
3. Waits 4 seconds for Flask to bind its port
4. Launches Chromium in kiosk mode pointing at `http://localhost:5000`
5. When Chromium exits, kills the Flask process

### Autostart on Boot

An XDG desktop entry fires `start.sh` every time the Pi desktop session starts:

```
~/.config/autostart/mbta-tracker.desktop
```

```ini
[Desktop Entry]
Type=Application
Name=MBTA Tracker
Terminal=false
Exec=/home/pi/mbta-train-tracker/start.sh
```

The Pi is configured to auto-login to the desktop (no password prompt on boot), so the sequence is: power on → desktop login → autostart fires → kiosk opens.

### Updating the App on the Pi

```bash
ssh pi@mbta-tracker.local
cd ~/mbta-train-tracker && git pull
sudo reboot
```

A reboot is the cleanest way to restart — it kills the old Flask process and re-runs the fetch thread with fresh station ID resolutions.

### Logs

All output from `start.sh` (including Flask stdout/stderr) is appended to:

```
~/mbta-train-tracker/startup.log
```

Useful for diagnosing autostart failures:

```bash
tail -50 ~/mbta-train-tracker/startup.log
```

### Hiding the Mouse Cursor

The kiosk has no mouse attached, so an idle pointer arrow would otherwise sit frozen on screen. Hiding it is **not** done in `start.sh` or in CSS — those approaches are X11-era and **silently do nothing** on this Pi.

**Why:** Raspberry Pi OS Trixie runs the **labwc Wayland compositor**, not X11. `unclutter`, `xset`, the CSS `cursor: none` rule, and the lightdm `xserver-command=X -nocursor` option all assume X11 and have no effect under Wayland. (You can confirm the compositor with `pgrep -a labwc` and the session with `echo $XDG_SESSION_TYPE` from inside the graphical session.)

**The fix** is a fully **transparent `XCURSOR` theme**, which both labwc and Xwayland honor — the cursor bitmap becomes invisible no matter which layer draws it. This is a one-time, per-Pi setup (it lives in the home directory, not the repo), so it must be re-run after reimaging the Pi:

```bash
cd ~/mbta-train-tracker
bash scripts/hide-cursor.sh
sudo reboot
```

`scripts/hide-cursor.sh` is idempotent. It:
1. Installs `xcursorgen` (provided by the `x11-apps` package on Trixie — it is no longer a standalone package).
2. Generates a 32x32 transparent PNG with pure Python (no image libraries needed).
3. Compiles it into an Xcursor file and installs it as the theme `~/.icons/blank`.
4. Adds `XCURSOR_THEME=blank` and `XCURSOR_SIZE=24` to `~/.config/labwc/environment`.

To undo it, remove those two lines from `~/.config/labwc/environment` and reboot.

---

## Common Gotchas

### Station name must match MBTA exactly
`find_station_parent_ids_for_routes` does a case-insensitive exact match against the MBTA stop `name` attribute. If the name is slightly off (e.g. "Govt Center" vs. "Government Center"), the station will fail to resolve and be skipped with a `[WARN]` in the log.

### Terminal-train filter uses `station_name`, not `display_name`
The filter that removes trains whose destination is the current station compares the MBTA headsign against `station_name` (the MBTA lookup name). If you set a custom `display_name`, the filter still uses the original `station_name` so it stays accurate.

### Green Line branch letter
All Green Line route IDs (`Green-B/C/D/E`) are collapsed into a single `"Green"` display row. The branch letter is appended to the headsign: `"Boston College"` becomes `"Boston College (B)"`. The guard `if f"({branch})" not in hs` prevents double-appending if the API already includes the letter.

### Template changes require `auto_reload = True`
Flask with `debug=False` compiles and caches Jinja2 templates in memory at startup. `app.jinja_env.auto_reload = True` ensures the template is re-read from disk on each request. Without this, changes to `index.html` are invisible until the server restarts.

### Chromium binary name on Pi OS Bookworm
The binary is `chromium`, not `chromium-browser`. `start.sh` already uses the correct name.

### `set -e` + `xset`
`xset` commands can fail on some display configurations. All `xset` calls in `start.sh` are followed by `|| true` to prevent `set -e` from silently aborting the script.
