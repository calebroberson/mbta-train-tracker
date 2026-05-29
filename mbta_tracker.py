#!/usr/bin/env python3
import os
import sys
import time
import threading
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional
import pytz
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

load_dotenv()  # must run before any os.getenv call

MBTA_API_BASE = "https://api-v3.mbta.com"
API_KEY = os.getenv("MBTA_API_KEY")  # optional, but raises rate limits when provided
TZ = pytz.timezone("America/New_York")

SESSION = requests.Session()  # reuse TCP connection across requests
if API_KEY:
    SESSION.headers.update({"x-api-key": API_KEY})  # attach once; all requests inherit it

app = Flask(__name__)
app.jinja_env.auto_reload = True


# --------- Configuration ---------
# Add or remove stations here. Each entry specifies the display name and the
# MBTA route IDs to track at that station.
CONFIG = [
    {"station_name": "Bowdoin",           "routes": ["Blue"],                                           "min_walk_mins": 2},
    {"station_name": "Haymarket",         "routes": ["Orange"],                                         "min_walk_mins": 8},
    {"station_name": "Park Street",       "routes": ["Red"],                                     "min_walk_mins": 6},
    {"station_name": "Park Street",       "routes": ["Green-B", "Green-C", "Green-D", "Green-E"], "min_walk_mins": 6},
    {"station_name": "Government Center", "routes": ["Green-B", "Green-C", "Green-D", "Green-E"],       "min_walk_mins": 6},
]

POLL_SECONDS = 30              # seconds between prediction refreshes
MAX_PREDICTIONS_PER_BUCKET = 8  # max arrivals shown per route per station
HTTP_TIMEOUT = 15              # seconds before an API request is aborted

ROUTE_COLORS = {
    "Blue":   "#003DA5",
    "Orange": "#ED8B00",
    "Red":    "#DA291C",
    "Green":  "#00843D",
}


# --------- Shared display state ---------
# Written by the background fetch thread, read by Flask request handlers.
_display_lock = threading.Lock()
_display_data: dict = {"stations": [], "last_updated": None, "error": None}


# --------- API helpers ---------

def mbta_get(path: str, params: Dict) -> dict:
    """
    GET an MBTA API endpoint, retrying up to 3 times on failure.

    Respects Retry-After on 429 responses. Returns a dict with empty 'data'
    and 'included' lists on unrecoverable failure so callers can always
    iterate the result without an extra None check.
    """
    url = f"{MBTA_API_BASE}{path}"

    for attempt in range(3):
        try:
            r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT)

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = int(retry_after) if retry_after else 1  # honour server hint or fall back to 1s
                print(f"[WARN] 429 Too Many Requests. Waiting {wait}s before retry...")
                time.sleep(wait)
                continue

            r.raise_for_status()  # raise for any other non-2xx status
            return r.json()

        except requests.RequestException as e:
            if attempt == 2:  # all retries exhausted
                print(f"[ERROR] Request failed after retries: {e}", file=sys.stderr)
                return {"data": [], "included": []}
            print(f"[WARN] Request error: {e}. Retrying in 1s...")
            time.sleep(1)

    return {"data": [], "included": []}  # unreachable in practice; satisfies the type checker


def find_station_parent_ids_for_routes(station_name: str, route_ids: List[str]) -> List[str]:
    """
    Resolve a station name to its MBTA parent place ID(s).

    Each station has a parent 'place-*' ID that groups all its platforms. We
    fetch stops for each route, match by name, and collect parent IDs (falling
    back to the stop ID itself when no parent is set).
    """
    parent_ids = set()  # set deduplicates IDs that appear across multiple routes
    for rid in route_ids:
        stops = mbta_get(
            "/stops",
            params={
                "filter[route]": rid,
                "page[limit]": 200,  # generous limit to capture all platforms
                "fields[stop]": "name,parent_station",
            },
        ).get("data", [])

        for s in stops:
            attrs = s.get("attributes", {})
            name = attrs.get("name", "")
            parent = attrs.get("parent_station")  # None for top-level place stops
            if name.lower() == station_name.lower():
                parent_ids.add(parent if parent else s.get("id"))  # prefer parent when present

    return sorted(parent_ids)  # sorted for a stable, predictable order


def fetch_predictions(stop_id: str, route_ids: List[str]):
    """
    Fetch live predictions for a parent stop, batching all routes in one request.

    Includes trip entities so headsigns (destination names) are available in
    the same response. Returns (data, included) where data is a list of
    prediction records and included is a list of related trip entities.
    """
    params = {
        "filter[stop]": stop_id,
        "filter[route]": ",".join(route_ids),  # comma-joined list batches multiple routes in one call
        "sort": "arrival_time,departure_time",  # soonest predictions first
        "page[limit]": 100,  # enough headroom after per-station walk-time filtering
        "include": "trip",  # sideload trips so headsigns are available without a second request
        "fields[prediction]": "arrival_time,departure_time,direction_id,stop,trip,route",  # sparse fieldset
        "fields[trip]": "headsign",
    }
    j = mbta_get("/predictions", params=params)
    return j.get("data", []), j.get("included", [])


def minutes_until(iso_str: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    """
    Return whole minutes from now until the given ISO 8601 timestamp.

    Clamps to 0 for past arrivals. Returns None on falsy or unparsable input.
    The optional 'now' parameter exists for test injection.
    """
    if not iso_str:
        return None
    try:
        target = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))  # normalise trailing 'Z' to a UTC offset
        now = now or datetime.now(timezone.utc)
        delta_sec = (target - now).total_seconds()
        return max(0, int(delta_sec // 60))  # floor to whole minutes; clamp negatives to 0
    except Exception:
        return None


# --------- Flask routes ---------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data")
def data():
    with _display_lock:
        return jsonify(_display_data)


# --------- Fetch loop ---------

def _fetch_loop(resolved_targets: list):
    """
    Runs forever in a background thread, writing fresh prediction data to
    _display_data every POLL_SECONDS. Errors are caught so the thread never dies.
    """
    route_order = ["Blue", "Orange", "Red", "Green"]  # fixed display order

    while True:
        try:
            stations_out = []

            for t in resolved_targets:
                station_name = t["station_name"]
                parent_ids = t["parent_ids"]
                routes = t["routes"]
                min_walk_mins = t.get("min_walk_mins", 0)
                filter_name = t.get("filter_name", station_name)  # canonical MBTA name for headsign comparison
                if not parent_ids:
                    continue

                # Accumulate (minutes, headsign) pairs keyed by display route name.
                # A station can have multiple parent IDs (e.g. separate platforms),
                # so we loop over all of them and merge into the same buckets.
                buckets: Dict[str, list] = {}

                for pid in parent_ids:
                    preds, included = fetch_predictions(pid, routes)

                    # Build a trip_id -> headsign lookup from the sideloaded trip entities
                    trip_headsign: Dict[str, str] = {}
                    for inc in included or []:  # 'included' can be None when no trips are sideloaded
                        if inc.get("type") == "trip":
                            trip_headsign[inc["id"]] = inc.get("attributes", {}).get("headsign", "")

                    for p in preds or []:
                        attrs = p.get("attributes", {})
                        rel = p.get("relationships", {})

                        # Navigate the JSON:API relationship chain to reach the route ID string
                        rid = rel.get("route", {}).get("data", {}).get("id")
                        iso = attrs.get("arrival_time") or attrs.get("departure_time")  # prefer arrival time
                        if not rid or not iso:
                            continue

                        mins = minutes_until(iso)
                        if mins is None:
                            continue

                        display_rid = "Green" if rid.startswith("Green-") else rid  # collapse all branches into one row

                        trip_id = rel.get("trip", {}).get("data", {}).get("id")
                        hs = trip_headsign.get(trip_id, "")
                        if hs and hs.lower() == filter_name.lower():
                            continue  # train terminates at this station; can't board it
                        if rid.startswith("Green-"):
                            try:
                                branch = rid.split("-")[1]  # extract letter from e.g. "Green-B" -> "B"
                                if hs and f"({branch})" not in hs:  # guard against double-appending
                                    hs = f"{hs} ({branch})"
                            except Exception:
                                pass

                        buckets.setdefault(display_rid, []).append((mins, hs))

                # Convert buckets to a JSON-serialisable list of route dicts
                routes_out = []
                for display_rid in [r for r in route_order if r in buckets]:  # preserve fixed display order
                    # Deduplicate, drop trains reachable can't catch, sort by time, then cap at N
                    items = sorted({p for p in buckets[display_rid] if p[0] > min_walk_mins}, key=lambda x: x[0])[:MAX_PREDICTIONS_PER_BUCKET]
                    routes_out.append({
                        "name": display_rid,
                        "color": ROUTE_COLORS.get(display_rid, "#888888"),
                        "arrivals": [{"mins": m, "headsign": h} for m, h in items],
                    })

                stations_out.append({"name": station_name, "routes": routes_out})

            _now = datetime.now(TZ)
            now_str = f"{_now.strftime('%B')} {_now.day}, {_now.year}  {int(_now.strftime('%I'))}:{_now.strftime('%M %p %Z')}"
            with _display_lock:
                _display_data["stations"] = stations_out
                _display_data["last_updated"] = now_str
                _display_data["error"] = None

        except Exception as e:
            print(f"[ERROR] Fetch loop error: {e}", file=sys.stderr)
            with _display_lock:
                _display_data["error"] = str(e)

        time.sleep(POLL_SECONDS)


# --------- Entry point ---------

def main():
    """
    Resolve station IDs once, then start the background fetch thread and the
    Flask web server. The server blocks until the process is killed (Ctrl+C).
    """
    resolved_targets = []
    for item in CONFIG:
        station = item["station_name"]
        routes = item["routes"]
        parent_ids = find_station_parent_ids_for_routes(station, routes)
        if not parent_ids:
            print(f"[WARN] Could not find any parent stop ids for '{station}' (routes: {routes})")
        display = item.get("display_name", station)  # fall back to station_name if no display_name
        resolved_targets.append({"station_name": display, "filter_name": station, "routes": routes, "parent_ids": parent_ids, "min_walk_mins": item.get("min_walk_mins", 0)})

    if all(len(t["parent_ids"]) == 0 for t in resolved_targets):  # every station failed to resolve
        print("[FATAL] No stations resolved. Check station names or network connectivity.")
        sys.exit(1)

    print("Resolved stations:")
    for t in resolved_targets:
        print(f"  - {t['station_name']}: parents {t['parent_ids']} (routes: {', '.join(t['routes'])})")

    # Start fetch loop as a daemon thread so it exits automatically when the main process does
    fetch_thread = threading.Thread(target=_fetch_loop, args=(resolved_targets,), daemon=True)
    fetch_thread.start()
    print("Fetch thread started. Opening http://localhost:5000 …")

    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
