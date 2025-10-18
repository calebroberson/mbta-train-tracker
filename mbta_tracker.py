#!/usr/bin/env python3
import os
import sys
import time
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import pytz

MBTA_API_BASE = "https://api-v3.mbta.com"
API_KEY = os.getenv("MBTA_API_KEY")  # optional but recommended
TZ = pytz.timezone("America/New_York")

SESSION = requests.Session()
if API_KEY:
    SESSION.headers.update({"x-api-key": API_KEY})

# --------- Configuration (what you asked for) ---------
# We’ll search for the station by its public name, then filter predictions per route and direction.
CONFIG = [
    # Bowdoin — Blue line (outbound only)
    {"station_name": "Bowdoin", "routes": ["Blue"], "directions": ["outbound"]},

    # Haymarket — Orange line (both)
    {"station_name": "Haymarket", "routes": ["Orange"], "directions": ["inbound", "outbound"]},

    # Park Street — Red line (both)
    {"station_name": "Park Street", "routes": ["Red"], "directions": ["inbound", "outbound"]},

    # Park Street — Green line (both) -> cover all branches that serve Park Street
    {"station_name": "Park Street", "routes": ["Green-B", "Green-C", "Green-D", "Green-E"], "directions": ["inbound", "outbound"]},

    # Government Center — Green line (both)
    {"station_name": "Government Center", "routes": ["Green-B", "Green-C", "Green-D", "Green-E"], "directions": ["inbound", "outbound"]},
]

POLL_SECONDS = 30
MAX_PREDICTIONS_PER_BUCKET = 5  # show top N per (station, route, direction)
HTTP_TIMEOUT = 15

# --------- Helper functions ---------
def mbta_get(path: str, params: Dict) -> dict:
    """GET wrapper with basic error handling."""
    url = f"{MBTA_API_BASE}{path}"
    try:
        r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"[ERROR] Request failed: {e}", file=sys.stderr)
        return {"data": []}

def get_route_direction_map(route_id: str) -> Dict[str, int]:
    """
    For a route, return a mapping like {'inbound': 1, 'outbound': 0} or whatever
    the route actually labels its directions as. If the route uses different labels
    (e.g., 'Eastbound'/'Westbound'), we still map the conventional inbound/outbound
    to 1/0 when possible.
    """
    # Cache per run to avoid repeated calls
    if not hasattr(get_route_direction_map, "_cache"):
        get_route_direction_map._cache = {}
    cache = get_route_direction_map._cache

    if route_id in cache:
        return cache[route_id]

    data = mbta_get(f"/routes/{route_id}", params={"fields[route]": "direction_names"})
    direction_names = []
    try:
        direction_names = data["data"]["attributes"]["direction_names"]  # e.g., ["Outbound","Inbound"]
    except Exception:
        direction_names = ["Outbound", "Inbound"]  # sensible default

    mapping = {}
    # Build a normalized name->id map (lowercased)
    for idx, name in enumerate(direction_names):
        mapping[name.lower()] = idx

    # Heuristic: expose conventional keys inbound/outbound even if labels are different
    # If the route explicitly uses inbound/outbound, great. Otherwise we map by position.
    result = {}
    if "inbound" in mapping and "outbound" in mapping:
        result["inbound"] = mapping["inbound"]
        result["outbound"] = mapping["outbound"]
    else:
        # Many MBTA heavy rail lines use [Outbound, Inbound]
        # idx 0 -> outbound-ish, idx 1 -> inbound-ish
        result["outbound"] = 0 if len(direction_names) > 0 else 0
        result["inbound"] = 1 if len(direction_names) > 1 else 1

    cache[route_id] = result
    return result

def find_station_parent_ids_for_routes(station_name: str, route_ids: List[str]) -> List[str]:
    """
    Return unique parent station ids (e.g., 'place-pktrm' for Park Street)
    that match the station_name for any of the provided routes.
    """
    parent_ids = set()
    for rid in route_ids:
        # Pull stops for this route, then match those whose name matches the station.
        # We request a generous page limit to capture all child stops/platforms.
        stops = mbta_get(
            "/stops",
            params={
                "filter[route]": rid,
                "page[limit]": 200,
                "fields[stop]": "name,parent_station"
            },
        ).get("data", [])

        for s in stops:
            attrs = s.get("attributes", {})
            name = attrs.get("name", "")
            parent = attrs.get("parent_station")
            if name.lower() == station_name.lower():
                parent_ids.add(parent if parent else s.get("id"))
    return sorted(parent_ids)

def iso_to_local_str(iso_str: str) -> str:
    if not iso_str:
        return ""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(TZ).strftime("%#I:%M:%S %p")

def fetch_predictions(stop_id: str, route_id: str, direction_id: Optional[int]) -> List[dict]:
    """
    Fetch predictions for a station (parent stop id like 'place-pktrm').
    We filter by route and (optionally) direction_id.
    """
    params = {
        "filter[stop]": stop_id,
        "filter[route]": route_id,
        "include": "route,stop,trip",
        "sort": "arrival_time,departure_time",
        "page[limit]": 25,
        "fields[prediction]": "arrival_time,departure_time,direction_id,stop,trip,route",
        "fields[trip]": "headsign",
        "fields[route]": "long_name,short_name"
    }
    if direction_id is not None:
        params["filter[direction_id]"] = direction_id

    j = mbta_get("/predictions", params=params)
    return j.get("data", [])

def summarize_prediction(p: dict, default_headsign: str = "") -> Tuple[str, str, int]:
    attrs = p.get("attributes", {})
    arr = attrs.get("arrival_time")
    dep = attrs.get("departure_time")
    when_iso = arr or dep
    when_local = iso_to_local_str(when_iso) if when_iso else "—"
    dir_id = attrs.get("direction_id", -1)

    # Try to extract headsign from included trip if present
    headsign = default_headsign
    # Fallback: MBTA includes trip relationship id; headsign sometimes shows up in included data,
    # but to keep this lightweight we’ll prefer times; headsign may not always be available.
    return (when_local, headsign, dir_id)

def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

def main():
    # Resolve station parent ids per configuration
    resolved_targets = []  # list of dicts with: station_name, route_id, directions, parent_ids
    for item in CONFIG:
        station = item["station_name"]
        routes = item["routes"]
        dirs = item["directions"]

        parent_ids = find_station_parent_ids_for_routes(station, routes)
        if not parent_ids:
            print(f"[WARN] Could not find any parent stop ids for '{station}' (routes: {routes})")
        resolved_targets.append({
            "station_name": station,
            "routes": routes,
            "directions": dirs,
            "parent_ids": parent_ids
        })

    if all(len(t["parent_ids"]) == 0 for t in resolved_targets):
        print("[FATAL] No stations resolved. Check station names or network connectivity.")
        sys.exit(1)

    print("Resolved stations:")
    for t in resolved_targets:
        print(f"  - {t['station_name']}: parents {t['parent_ids']} (routes: {', '.join(t['routes'])})")
    print("\nStarting live polling… Press Ctrl+C to stop.")

    try:
        while True:
            now_local = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
            print_header(f"MBTA Live Predictions @ {now_local}")

            for t in resolved_targets:
                station = t["station_name"]
                parent_ids = t["parent_ids"]
                if not parent_ids:
                    continue

                for rid in t["routes"]:
                    dir_map = get_route_direction_map(rid)
                    for human_dir in t["directions"]:
                        direction_id = dir_map.get(human_dir.lower())
                        # Some routes might not provide a clear mapping; if missing, fetch both
                        dir_ids_to_try = [direction_id] if direction_id is not None else [0, 1]

                        for pid in parent_ids:
                            # Gather predictions across the direction ids we want
                            bucket = []
                            for d_id in dir_ids_to_try:
                                preds = fetch_predictions(pid, rid, d_id)
                                for p in preds:
                                    when_local, headsign, dir_id = summarize_prediction(p)
                                    if when_local != "—":
                                        bucket.append((when_local, headsign, dir_id))

                            # Deduplicate and sort by time string (already chronological by API, but safe to sort)
                            # Note: sorting lexicographically is fine because we format as %I:%M:%S %p each tick.
                            bucket = sorted(set(bucket), key=lambda x: x[0])[:MAX_PREDICTIONS_PER_BUCKET]

                            # Pretty print
                            dir_label = human_dir.capitalize()
                            print(f"{station} | Route {rid} | {dir_label}")
                            if bucket:
                                for when_local, headsign, dir_id in bucket:
                                    hs = f" — {headsign}" if headsign else ""
                                    print(f"  • {when_local}{hs}")
                            else:
                                print("  (no upcoming predictions)")
            # Sleep until next poll
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping. Bye!")

if __name__ == "__main__":
    main()
