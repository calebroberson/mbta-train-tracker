import responses
from mbta_tracker import find_station_parent_ids_for_routes, MBTA_API_BASE

@responses.activate
def test_find_station_parents_returns_unique_sorted():
    rid = "Red"
    responses.add(
        responses.GET,
        f"{MBTA_API_BASE}/stops",
        json={
            "data": [
                {"id": "place-pktrm", "attributes": {"name": "Park Street", "parent_station": None}},
                {"id": "70105", "attributes": {"name": "Park Street", "parent_station": "place-pktrm"}},
                {"id": "random", "attributes": {"name": "Other", "parent_station": None}},
            ]
        },
        status=200,
    )

    result = find_station_parent_ids_for_routes("Park Street", [rid])
    assert result == ["place-pktrm"]


@responses.activate
def test_find_station_parents_empty_if_not_found():
    rid = "Orange"
    responses.add(
        responses.GET,
        f"{MBTA_API_BASE}/stops",
        json={"data": [{"id": "1", "attributes": {"name": "Other", "parent_station": None}}]},
        status=200,
    )

    result = find_station_parent_ids_for_routes("Nonexistent", [rid])
    assert result == []
