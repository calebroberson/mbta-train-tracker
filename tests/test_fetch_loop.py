import pytest
from unittest.mock import patch
from freezegun import freeze_time
from mbta_tracker import _fetch_loop, _display_data, _display_lock


@pytest.fixture(autouse=True)
def reset_display_data():
    with _display_lock:
        _display_data["stations"] = []
        _display_data["last_updated"] = None
        _display_data["error"] = None
    yield


def _pred(route_id, arrival_iso, trip_id):
    return {
        "attributes": {"arrival_time": arrival_iso, "departure_time": None},
        "relationships": {
            "route": {"data": {"id": route_id}},
            "trip": {"data": {"id": trip_id}},
        },
    }


def _trip(trip_id, headsign):
    return {"type": "trip", "id": trip_id, "attributes": {"headsign": headsign}}


def run_one_iteration(resolved_targets, fetch_return=None):
    """Run _fetch_loop for exactly one iteration; time.sleep raises SystemExit to stop the loop."""
    mock_preds = fetch_return or ([], [])
    with patch("mbta_tracker.fetch_predictions", return_value=mock_preds):
        with patch("time.sleep", side_effect=SystemExit):
            with pytest.raises(SystemExit):
                _fetch_loop(resolved_targets)


@freeze_time("2026-01-01 12:00:00")
def test_prediction_appears_in_display_data():
    preds = [_pred("Red", "2026-01-01T12:10:00+00:00", "t1")]
    included = [_trip("t1", "Alewife")]

    run_one_iteration(
        [{"station_name": "Park Street", "parent_ids": ["place-pktrm"], "routes": ["Red"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    stations = _display_data["stations"]
    assert len(stations) == 1
    assert stations[0]["name"] == "Park Street"
    arrivals = stations[0]["routes"][0]["arrivals"]
    assert arrivals[0] == {"mins": 10, "headsign": "Alewife"}


@freeze_time("2026-01-01 12:00:00")
def test_green_branches_collapsed_under_green():
    preds = [
        _pred("Green-B", "2026-01-01T12:05:00+00:00", "tb"),
        _pred("Green-C", "2026-01-01T12:08:00+00:00", "tc"),
    ]
    included = [_trip("tb", "Boston College"), _trip("tc", "Cleveland Circle")]

    run_one_iteration(
        [{"station_name": "Gov Center", "parent_ids": ["place-gover"], "routes": ["Green-B", "Green-C"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    routes = _display_data["stations"][0]["routes"]
    assert len(routes) == 1
    assert routes[0]["name"] == "Green"
    headsigns = [a["headsign"] for a in routes[0]["arrivals"]]
    assert "Boston College (B)" in headsigns
    assert "Cleveland Circle (C)" in headsigns


@freeze_time("2026-01-01 12:00:00")
def test_green_branch_suffix_not_doubled():
    preds = [_pred("Green-B", "2026-01-01T12:05:00+00:00", "tb")]
    # headsign already contains "(B)" — should not be appended again
    included = [_trip("tb", "Boston College (B)")]

    run_one_iteration(
        [{"station_name": "Gov Center", "parent_ids": ["place-gover"], "routes": ["Green-B"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    headsigns = [a["headsign"] for a in _display_data["stations"][0]["routes"][0]["arrivals"]]
    assert headsigns == ["Boston College (B)"]


@freeze_time("2026-01-01 12:00:00")
def test_walk_time_filter_excludes_close_arrivals():
    preds = [
        _pred("Red", "2026-01-01T12:02:00+00:00", "t1"),  # 2 min — too close
        _pred("Red", "2026-01-01T12:10:00+00:00", "t2"),  # 10 min — reachable
    ]
    included = [_trip("t1", "Alewife"), _trip("t2", "Alewife")]

    run_one_iteration(
        [{"station_name": "Park Street", "parent_ids": ["place-pktrm"], "routes": ["Red"], "min_walk_mins": 5}],
        fetch_return=(preds, included),
    )

    arrivals = _display_data["stations"][0]["routes"][0]["arrivals"]
    assert len(arrivals) == 1
    assert arrivals[0]["mins"] == 10


@freeze_time("2026-01-01 12:00:00")
def test_station_with_no_parent_ids_is_skipped():
    run_one_iteration(
        [{"station_name": "Ghost", "parent_ids": [], "routes": ["Red"], "min_walk_mins": 0}],
    )
    assert _display_data["stations"] == []


@freeze_time("2026-01-01 12:00:00")
def test_route_display_order_is_fixed():
    preds = [
        _pred("Green-B", "2026-01-01T12:08:00+00:00", "tg"),
        _pred("Red",     "2026-01-01T12:05:00+00:00", "tr"),
    ]
    included = [_trip("tg", "Boston College"), _trip("tr", "Alewife")]

    run_one_iteration(
        [{"station_name": "Park Street", "parent_ids": ["place-pktrm"], "routes": ["Red", "Green-B"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    route_names = [r["name"] for r in _display_data["stations"][0]["routes"]]
    assert route_names == ["Red", "Green"]


@freeze_time("2026-01-01 12:00:00")
def test_arrivals_capped_at_max_predictions():
    preds = [_pred("Red", f"2026-01-01T12:{5 + i:02d}:00+00:00", f"t{i}") for i in range(10)]
    included = [_trip(f"t{i}", "Alewife") for i in range(10)]

    run_one_iteration(
        [{"station_name": "Park Street", "parent_ids": ["place-pktrm"], "routes": ["Red"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    arrivals = _display_data["stations"][0]["routes"][0]["arrivals"]
    assert len(arrivals) == 8  # MAX_PREDICTIONS_PER_BUCKET


@freeze_time("2026-01-01 12:00:00")
def test_last_updated_is_set_after_successful_fetch():
    run_one_iteration(
        [{"station_name": "Park Street", "parent_ids": ["place-pktrm"], "routes": ["Red"], "min_walk_mins": 0}],
    )
    assert _display_data["last_updated"] is not None


@freeze_time("2026-01-01 12:00:00")
def test_terminal_headsign_filtered_out():
    """A prediction whose headsign equals the station name should be dropped."""
    preds = [_pred("Blue", "2026-01-01T12:10:00+00:00", "t1")]
    included = [_trip("t1", "Bowdoin")]

    run_one_iteration(
        [{"station_name": "Bowdoin", "filter_name": "Bowdoin",
          "parent_ids": ["place-bodsq"], "routes": ["Blue"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    # All predictions were terminal — bucket is empty, so no route rows rendered
    assert _display_data["stations"][0]["routes"] == []


@freeze_time("2026-01-01 12:00:00")
def test_non_terminal_headsign_kept():
    """A prediction whose headsign differs from the station name should be kept."""
    preds = [_pred("Blue", "2026-01-01T12:10:00+00:00", "t1")]
    included = [_trip("t1", "Wonderland")]

    run_one_iteration(
        [{"station_name": "Bowdoin", "filter_name": "Bowdoin",
          "parent_ids": ["place-bodsq"], "routes": ["Blue"], "min_walk_mins": 0}],
        fetch_return=(preds, included),
    )

    arrivals = _display_data["stations"][0]["routes"][0]["arrivals"]
    assert arrivals[0]["headsign"] == "Wonderland"


def test_exception_sets_error_in_display_data():
    with patch("mbta_tracker.fetch_predictions", side_effect=RuntimeError("API down")):
        with patch("time.sleep", side_effect=SystemExit):
            with pytest.raises(SystemExit):
                _fetch_loop([{"station_name": "Park Street", "parent_ids": ["place-pktrm"], "routes": ["Red"], "min_walk_mins": 0}])

    assert _display_data["error"] == "API down"
