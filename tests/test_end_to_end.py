import responses
from mbta_tracker import get_route_direction_map, find_station_parent_ids_for_routes, fetch_predictions

@responses.activate
def test_end_to_end_minimal_flow():
    rid = "Red"
    responses.add(
        responses.GET,
        f"{getattr(__import__('mbta_tracker'), 'MBTA_API_BASE')}/routes/{rid}",
        json={"data": {"attributes": {"direction_names": ["Outbound", "Inbound"]}}},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{getattr(__import__('mbta_tracker'), 'MBTA_API_BASE')}/stops",
        json={"data": [{"id": "place-pktrm", "attributes": {"name": "Park Street", "parent_station": None}}]},
        status=200,
    )
    responses.add(
        responses.GET,
        f"{getattr(__import__('mbta_tracker'), 'MBTA_API_BASE')}/predictions",
        json={"data": [{"id": "1", "attributes": {"arrival_time": "2025-10-18T14:00:00Z", "direction_id": 1}}]},
        status=200,
    )

    mapping = get_route_direction_map(rid)
    assert mapping["inbound"] == 1

    parents = find_station_parent_ids_for_routes("Park Street", [rid])
    assert parents == ["place-pktrm"]

    preds = fetch_predictions("place-pktrm", rid, 1)
    assert len(preds) == 1
