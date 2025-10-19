#!/usr/bin/env python3
import os  # stdlib for env vars / paths
import sys  # stdlib for stderr printing / exit
import time  # stdlib for sleep/backoff
import requests  # HTTP client for MBTA API
from datetime import datetime, timezone  # timezone-aware datetimes
from typing import Dict, List, Optional, Tuple  # type hints
import pytz  # local timezone conversion
from dotenv import load_dotenv  # load .env file

load_dotenv()  # reads the .env file and sets environment variables on process start

MBTA_API_BASE = "https://api-v3.mbta.com"  # base URL for MBTA v3 API
API_KEY = os.getenv("MBTA_API_KEY")  # optional but recommended; improves rate limits
print(f"API_KEY loaded? {API_KEY is not None} (value hidden)")  # debug visibility only
TZ = pytz.timezone("America/New_York")  # local display timezone for Boston area

SESSION = requests.Session()  # reuse TCP connection across requests
if API_KEY:
    SESSION.headers.update({"x-api-key": API_KEY})  # attach API key header once up front


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

POLL_SECONDS = 30  # how often to poll MBTA in the main loop
MAX_PREDICTIONS_PER_BUCKET = 3  # show top N per (station, route) after sorting
HTTP_TIMEOUT = 15  # seconds; network timeout per HTTP call


# --------- Helper functions ---------
def mbta_get(path: str, params: Dict) -> dict:
    """\
    Perform a GET against the MBTA API with basic error handling and retry logic.

    - Honors 429 responses with `Retry-After` if present; otherwise short backoff.
    - Retries network errors up to 3 total attempts.
    - Returns a JSON dict with at least `data`/`included` keys on error fallback.

    Args:
        path: API path beginning with `/`, e.g. `/predictions`.
        params: Querystring parameters to include in the request.

    Returns:
        Parsed JSON dict from the response, or a minimal fallback on failure.
    """
    url = f"{MBTA_API_BASE}{path}"  # assemble full URL

    for attempt in range(3):  # Up to 3 total tries
        try:
            r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT)  # single HTTP GET

            # If server says "Too Many Requests"
            if r.status_code == 429:
                # Respect Retry-After if MBTA provides it
                retry_after = r.headers.get("Retry-After")  # may be a number of seconds
                if retry_after:
                    wait = int(retry_after)  # use server-provided delay
                else:
                    wait = 1  # default small backoff if header absent
                print(f"[WARN] 429 Too Many Requests. Waiting {wait}s before retry...")
                time.sleep(wait)  # pause before retrying
                continue  # retry

            # If another HTTP error (like 500, 404, etc), raise it
            r.raise_for_status()  # will throw for non-2xx status codes

            # Success!
            return r.json()  # parsed JSON payload

        except requests.RequestException as e:
            # If it's a network error or other issue
            if attempt == 2:  # last attempt; give up gracefully
                print(f"[ERROR] Request failed after retries: {e}", file=sys.stderr)
                return {"data": [], "included": []}  # consistent shape for callers
            print(f"[WARN] Request error: {e}. Retrying in 1s...")
            time.sleep(1)  # small generic backoff before next attempt

    # Fallback (just in case) — defensive; loop should already have returned
    return {"data": [], "included": []}


def get_route_direction_map(route_id: str) -> Dict[str, int]:
    """\
    Return a mapping for a route's human directions to MBTA `direction_id` integers.

    Many routes label directions as ["Outbound", "Inbound"] (index 0 and 1 respectively).
    We normalize these into keys `"inbound"` and `"outbound"` so the rest of the code
    can rely on semantic names regardless of route-specific labels.

    Args:
        route_id: MBTA route id (e.g., "Red", "Orange", "Green-D", "Blue").

    Returns:
        Dict like {"inbound": 1, "outbound": 0}. Values are integers 0/1.
    """
    # Cache per run to avoid repeated calls
    if not hasattr(get_route_direction_map, "_cache"):
        get_route_direction_map._cache = {}  # simple function attribute cache
    cache = get_route_direction_map._cache  # local alias for brevity

    if route_id in cache:
        return cache[route_id]  # return cached mapping if available

    data = mbta_get(f"/routes/{route_id}", params={"fields[route]": "direction_names"})  # fetch labels
    direction_names = []  # default container
    try:
        direction_names = data["data"]["attributes"]["direction_names"]  # e.g., ["Outbound","Inbound"]
    except Exception:
        direction_names = ["Outbound", "Inbound"]  # sensible default when absent/broken

    mapping = {}  # temp map of lowercase label -> index
    # Build a normalized name->id map (lowercased)
    for idx, name in enumerate(direction_names):
        mapping[name.lower()] = idx  # remember position for each label

    # Heuristic: expose conventional keys inbound/outbound even if labels are different
    # If the route explicitly uses inbound/outbound, great. Otherwise we map by position.
    result = {}  # final normalized mapping
    if "inbound" in mapping and "outbound" in mapping:
        result["inbound"] = mapping["inbound"]  # use explicit mapping
        result["outbound"] = mapping["outbound"]  # use explicit mapping
    else:
        # Many MBTA heavy rail lines use [Outbound, Inbound]
        # idx 0 -> outbound-ish, idx 1 -> inbound-ish
        result["outbound"] = 0 if len(direction_names) > 0 else 0  # fallback index
        result["inbound"] = 1 if len(direction_names) > 1 else 1  # fallback index

    cache[route_id] = result  # memoize for subsequent calls
    return result  # normalized mapping


def find_station_parent_ids_for_routes(station_name: str, route_ids: List[str]) -> List[str]:
    """\
    Resolve a station's MBTA parent place IDs for any of the provided route IDs.

    The MBTA API exposes child platform stops and their parent "place-*" ids.
    We look up all stops for each route and select those whose stop name matches
    the provided station name, then return their parent ids (or stop id if no parent).

    Args:
        station_name: Public-facing stop name (e.g., "Park Street").
        route_ids: List of route ids to search (e.g., ["Red", "Green-D"]).

    Returns:
        Sorted list of unique parent place ids (e.g., ["place-pktrm"]).
    """
    parent_ids = set()  # de-duplicate across routes
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
        ).get("data", [])  # extract list safely

        for s in stops:
            attrs = s.get("attributes", {})  # stop attributes
            name = attrs.get("name", "")  # public stop name
            parent = attrs.get("parent_station")  # parent place id or None
            if name.lower() == station_name.lower():  # case-insensitive match
                parent_ids.add(parent if parent else s.get("id"))  # prefer parent id if present
    return sorted(parent_ids)  # stable order for readability


def iso_to_local_str(iso_str: str) -> str:
    """\
    Convert an ISO8601 UTC string (possibly ending with 'Z') to a local time string.

    The output is formatted in the local Boston timezone and uses 12-hour clock
    (platform-appropriate directive for Windows via `%#I`).

    Args:
        iso_str: ISO timestamp string like "2025-10-18T15:00:00Z".

    Returns:
        Local time string like "11:00:00 AM", or empty string on falsy input.
    """
    if not iso_str:
        return ""  # guard for None/empty
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))  # parse as aware UTC
    return dt.astimezone(TZ).strftime("%#I:%M:%S %p")  # format for display


def fetch_predictions(stop_id: str, route_ids: list[str]) -> List[dict]:
    """\
    Fetch prediction entities for a single parent stop and multiple routes (batched).

    We call `/predictions` once with a comma-joined list of route ids and include
    `trip` entities so we can access headsigns for destination display.

    Args:
        stop_id: Parent place id like "place-pktrm".
        route_ids: List of route ids to include in one batched request.

    Returns:
        Tuple of (data, included):
          - data: list of prediction records
          - included: list of related entities (e.g., trips with headsigns)
    """
    params = {
        "filter[stop]": stop_id,  # restrict to the target station/place
        "filter[route]": ",".join(route_ids),  # batch multiple routes together
        "sort": "arrival_time,departure_time",  # soonest first
        "page[limit]": 10,  # we only need a handful; keeps payload small
        "include": "trip",  # include trip info so we can show headsigns
        "fields[prediction]": "arrival_time,departure_time,direction_id,stop,trip,route",  # slim payload
        "fields[trip]": "headsign",  # only need headsign from trip
    }
    j = mbta_get("/predictions", params=params)  # perform the API call
    return j.get("data", []), j.get("included", [])  # safe extraction with defaults


def summarize_prediction(p: dict, default_headsign: str = "") -> Tuple[Optional[int], str, int]:
    """\
    Reduce a raw prediction to (minutes_until, headsign, direction_id).

    This helper extracts arrival/departure ISO times, converts to minutes until
    arrival (floored to 0), and returns the headsign placeholder (caller may
    replace with resolved trip headsign) plus the numeric direction id.

    Args:
        p: A single prediction record from MBTA API.
        default_headsign: Fallback headsign if not resolved externally.

    Returns:
        Tuple: (minutes_until or None, headsign string, direction_id int).
    """
    attrs = p.get("attributes", {})  # prediction attributes
    arr = attrs.get("arrival_time")  # ISO arrival time
    dep = attrs.get("departure_time")  # ISO departure time
    when_iso = arr or dep  # prefer arrival; fallback to departure
    mins = minutes_until(when_iso) if when_iso else None  # compute minutes-until
    dir_id = attrs.get("direction_id", -1)  # 0/1 when present, else -1

    headsign = default_headsign  # (keep if you later wire in trip headsign)
    return (mins, headsign, dir_id)  # compact summary tuple


def minutes_until(iso_str: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    """\
    Compute whole minutes from `now` to the future ISO8601 timestamp (UTC-based).

    - Treats inputs as UTC (replacing trailing 'Z' when present).
    - Floors to 0 for past or near-past arrivals (never negative).
    - Returns None when the input is falsy or cannot be parsed.

    Args:
        iso_str: ISO timestamp string or None.
        now: Optional override for the current moment (useful in tests).

    Returns:
        Non-negative integer minutes until, or None if unparsable.
    """
    if not iso_str:
        return None  # no timestamp provided
    try:
        target = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))  # parse aware UTC
        now = now or datetime.now(timezone.utc)  # default to current UTC time
        delta_sec = (target - now).total_seconds()  # difference in seconds
        mins = int(delta_sec // 60)  # floor to whole minutes
        return max(0, mins)  # clamp negatives to 0
    except Exception:
        return None  # graceful failure on bad input


def print_header(title: str):
    """\
    Print a simple banner header around the provided title.

    Args:
        title: The string to center between divider lines.
    """
    print("\n" + "=" * 80)  # top divider
    print(title)  # the banner title text
    print("=" * 80)  # bottom divider
    
    
def is_green_branch(route_id: str) -> bool:
    """\
    Return True if the provided route id is a Green branch (Green-B/C/D/E).

    Args:
        route_id: Route identifier string (e.g., "Green-D", "Red").

    Returns:
        True for Green branches, False otherwise.
    """
    return route_id.startswith("Green-")  # simple prefix check


def main():
    """\
    Resolve station -> parent ids, poll predictions, and print grouped results.

    Behavior:
      - Resolves parent place ids for each configured station+routes.
      - In a loop, fetches predictions batched per station (all routes at once).
      - Groups all Green branches under a single "Green" display route.
      - Prints, per station, each route's next N arrivals in minutes with headsigns.

    Note:
      - The function runs indefinitely until KeyboardInterrupt (Ctrl+C).
    """
    # Resolve station parent ids per configuration (one-time)
    resolved_targets = []  # list of dicts with: station_name, route_ids, directions, parent_ids
    for item in CONFIG:
        station = item["station_name"]  # human station name
        routes = item["routes"]  # routes to consider for that station
        dirs = item["directions"]  # preserved (may be unused in current print mode)

        parent_ids = find_station_parent_ids_for_routes(station, routes)  # place ids
        if not parent_ids:
            print(f"[WARN] Could not find any parent stop ids for '{station}' (routes: {routes})")
        resolved_targets.append({
            "station_name": station,
            "routes": routes,
            "directions": dirs,
            "parent_ids": parent_ids
        })  # accumulate target config+ids

    if all(len(t["parent_ids"]) == 0 for t in resolved_targets):
        print("[FATAL] No stations resolved. Check station names or network connectivity.")  # hard stop
        sys.exit(1)  # exit with failure

    print("Resolved stations:")  # debug listing for visibility
    for t in resolved_targets:
        print(f"  - {t['station_name']}: parents {t['parent_ids']} (routes: {', '.join(t['routes'])})")
    print("\nStarting live polling… Press Ctrl+C to stop.")  # indicate loop start

    try:
        while True:  # continuous polling loop
            now_local = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")  # timestamp label
            print_header(f"MBTA Live Predictions @ {now_local}")  # cycle header

            # Stable display order; Green branches are grouped under "Green"
            route_order = ["Blue", "Orange", "Red", "Green"]  # fixed presentation order

            for t in resolved_targets:
                station = t["station_name"]  # name to print
                parent_ids = t["parent_ids"]  # list of place ids
                routes = t["routes"]  # routes to include for this station
                if not parent_ids:
                    continue  # skip stations we couldn't resolve

                # ---- Gather all predictions across all parent_ids for this station ----
                # buckets: display_route_id -> list[(mins, headsign)]
                buckets: Dict[str, list] = {}  # route -> list of (minutes, headsign)

                for pid in parent_ids:
                    # One batched predictions call per station/place id
                    preds, included = fetch_predictions(pid, routes)  # returns (data, included)

                    # Map trip_id -> headsign (if included)
                    trip_headsign: Dict[str, str] = {}  # lookup for destination text
                    for inc in included or []:
                        if inc.get("type") == "trip":
                            trip_headsign[inc["id"]] = inc.get("attributes", {}).get("headsign", "")  # may be ""

                    # Partition predictions locally by route & direction
                    for p in preds or []:
                        attrs = p.get("attributes", {})  # prediction attributes
                        rel = p.get("relationships", {})  # relationships bag

                        rid = rel.get("route", {}).get("data", {}).get("id")   # e.g. "Green-D" or "Red"
                        iso = attrs.get("arrival_time") or attrs.get("departure_time")  # when to use
                        if not rid or not iso:
                            continue  # skip incomplete predictions

                        mins = minutes_until(iso)  # compute minutes until arrival/departure
                        if mins is None:
                            continue  # drop unparsable values

                        # Group all Green branches under a single "Green" display route
                        display_rid = "Green" if rid.startswith("Green-") else rid  # collapse branches

                        # Head-sign, optionally append branch letter for Green
                        trip_id = rel.get("trip", {}).get("data", {}).get("id")  # trip relation id
                        hs = trip_headsign.get(trip_id, "")  # destination text if available
                        if rid.startswith("Green-"):
                            try:
                                branch = rid.split("-")[1]  # B/C/D/E
                                if hs and f"({branch})" not in hs:
                                    hs = f"{hs} ({branch})"  # make branch explicit for clarity
                            except Exception:
                                pass  # be resilient if route id is malformed

                        buckets.setdefault(display_rid, []).append((mins, hs))  # append to route bucket

                # ---- Print one block per station, then per route (no direction split) ----
                print(station)  # station header

                for display_rid in [r for r in route_order if r in buckets]:
                    # Sort by minutes, dedupe exact duplicates, then take top N
                    items = sorted(set(buckets[display_rid]), key=lambda x: x[0])[:MAX_PREDICTIONS_PER_BUCKET]  # trim

                    # Route subheader with "Line"
                    print(f"  {display_rid} Line")  # e.g., "Red Line", "Green Line"
                    for mins, hs in items:
                        suffix = f" — {hs}" if hs else ""  # include headsign when present
                        print(f"    • {mins} min{suffix}")  # bullet list item

            # Sleep until next poll
            time.sleep(POLL_SECONDS)  # pacing for the outer loop
    except KeyboardInterrupt:
        print("\nStopping. Bye!")  # graceful exit on Ctrl+C


if __name__ == "__main__":
    main()  # entry point