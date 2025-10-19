#!/usr/bin/env python3
import os
import sys
import time
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import pytz
from dotenv import load_dotenv

load_dotenv()  # reads the .env file and sets environment variables

MBTA_API_BASE = "https://api-v3.mbta.com"
API_KEY = os.getenv("MBTA_API_KEY")  # optional but recommended
print(f"API_KEY loaded? {API_KEY is not None} (value hidden)")
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

    # Park Street — Red line (both) and Green line (both)
    {"station_name": "Park Street", "routes": ["Red", "Green-B", "Green-C", "Green-D", "Green-E"], "directions": ["inbound", "outbound"]},

    # Government Center — Green line (both)
    {"station_name": "Government Center", "routes": ["Green-B", "Green-C", "Green-D", "Green-E"], "directions": ["inbound", "outbound"]},
]

POLL_SECONDS = 30
MAX_PREDICTIONS_PER_BUCKET = 3  # show top N per (station, route, direction)
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

def fetch_predictions(stop_id: str, route_ids: list[str]) -> List[dict]:
    """
    Fetch predictions for a station (parent stop id like 'place-pktrm').
    We filter by route and (optionally) direction_id.
    """
    params = {
        "filter[stop]": stop_id,
        "filter[route]": ",".join(route_ids),
        "sort": "arrival_time,departure_time",
        "page[limit]": 10,
        "include": "trip",
        "fields[prediction]": "arrival_time,departure_time,direction_id,stop,trip,route",
        "fields[trip]": "headsign",
    }
    j = mbta_get("/predictions", params=params)
    return j.get("data", []), j.get("included", [])

def summarize_prediction(p: dict, default_headsign: str = "") -> Tuple[Optional[int], str, int]:
    attrs = p.get("attributes", {})
    arr = attrs.get("arrival_time")
    dep = attrs.get("departure_time")
    when_iso = arr or dep
    mins = minutes_until(when_iso) if when_iso else None
    dir_id = attrs.get("direction_id", -1)

    headsign = default_headsign  # (keep if you later wire in trip headsign)
    return (mins, headsign, dir_id)

def minutes_until(iso_str: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    """
    Return whole minutes from 'now' until the given ISO time.
    Uses UTC for comparison because MBTA times are in UTC.
    Returns None if iso_str is falsy or unparsable.
    Floors to 0 for past/near-past times.
    """
    if not iso_str:
        return None
    try:
        target = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))  # aware UTC
        now = now or datetime.now(timezone.utc)
        delta_sec = (target - now).total_seconds()
        mins = int(delta_sec // 60)  # floor
        return max(0, mins)
    except Exception:
        return None


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    
def is_green_branch(route_id: str) -> bool:
    return route_id.startswith("Green-")

def main():
    # Resolve station parent ids per configuration (one-time)
    resolved_targets = []  # list of dicts with: station_name, route_ids, directions, parent_ids
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

            # Stable display order; Green branches are grouped under "Green"
            route_order = ["Blue", "Orange", "Red", "Green"]

            for t in resolved_targets:
                station = t["station_name"]
                parent_ids = t["parent_ids"]
                routes = t["routes"]
                if not parent_ids:
                    continue

                # ---- Gather all predictions across all parent_ids for this station ----
                # buckets: display_route_id -> list[(mins, headsign)]
                buckets: Dict[str, list] = {}

                for pid in parent_ids:
                    # One batched predictions call per station/place id
                    preds, included = fetch_predictions(pid, routes)

                    # Map trip_id -> headsign (if included)
                    trip_headsign: Dict[str, str] = {}
                    for inc in included or []:
                        if inc.get("type") == "trip":
                            trip_headsign[inc["id"]] = inc.get("attributes", {}).get("headsign", "")

                    # Partition predictions locally by route & direction
                    for p in preds or []:
                        attrs = p.get("attributes", {})
                        rel = p.get("relationships", {})

                        rid = rel.get("route", {}).get("data", {}).get("id")   # e.g. "Green-D" or "Red"
                        iso = attrs.get("arrival_time") or attrs.get("departure_time")
                        if not rid or not iso:
                            continue

                        mins = minutes_until(iso)
                        if mins is None:
                            continue

                        # Group all Green branches under a single "Green" display route
                        display_rid = "Green" if rid.startswith("Green-") else rid

                        # Head-sign, optionally append branch letter for Green
                        trip_id = rel.get("trip", {}).get("data", {}).get("id")
                        hs = trip_headsign.get(trip_id, "")
                        if rid.startswith("Green-"):
                            try:
                                branch = rid.split("-")[1]  # B/C/D/E
                                if hs and f"({branch})" not in hs:
                                    hs = f"{hs} ({branch})"
                            except Exception:
                                pass

                        buckets.setdefault(display_rid, []).append((mins, hs))

                # ---- Print one block per station, then per route (no direction split) ----
                print(station)

                for display_rid in [r for r in route_order if r in buckets]:
                    # Sort by minutes, dedupe exact duplicates, then take top N
                    items = sorted(set(buckets[display_rid]), key=lambda x: x[0])[:MAX_PREDICTIONS_PER_BUCKET]

                    # Route subheader with "Line"
                    print(f"  {display_rid} Line")
                    for mins, hs in items:
                        suffix = f" — {hs}" if hs else ""
                        print(f"    • {mins} min{suffix}")

            # Sleep until next poll
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping. Bye!")

if __name__ == "__main__":
    main()
