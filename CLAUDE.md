# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate virtual environment (Windows)
venv\Scripts\Activate.ps1

# Run the tracker
python mbta_tracker.py

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_time_utils.py

# Run a single test by name
pytest tests/test_time_utils.py::test_iso_to_local_str_converts_correctly
```

## Environment

Copy `.env` and set your MBTA API key (optional, but improves rate limits):
```
MBTA_API_KEY=your_key_here
```

## Architecture

The entire application lives in a single file: `mbta_tracker.py`. There are no submodules.

**Data flow:**
1. `CONFIG` (top of file) declares which stations, routes, and directions to watch.
2. On startup, `main()` calls `find_station_parent_ids_for_routes()` once to resolve human station names (e.g., "Park Street") into MBTA parent place IDs (e.g., `place-pktrm`) via the `/stops` API.
3. In a loop every `POLL_SECONDS`, `fetch_predictions()` makes one batched `/predictions` call per station (all routes in a single request) and returns raw prediction records plus included trip entities.
4. Results are grouped into display buckets by route, with all Green branches (Green-B/C/D/E) collapsed under a single `"Green"` key. Branch letters are appended to headsigns for disambiguation.

**Key design details:**
- `requests.Session` is module-level and reuses TCP connections; the API key header is attached once at startup.
- `get_route_direction_map()` caches results as a function attribute (`_cache`) to avoid repeated API calls per run.
- `minutes_until()` accepts an optional `now` parameter for testability.
- `iso_to_local_str()` uses `%#I` (Windows-specific) for zero-padded hour suppression — use `%-I` on Linux/macOS if porting.

## Tests

Tests live in `tests/` and use `pytest` with the `responses` library for HTTP mocking and `freezegun` for time control. Tests import directly from `mbta_tracker` (no package install needed; run from the repo root).
